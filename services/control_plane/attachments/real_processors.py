"""M05 真实多模态处理器（§2.1 技术栈 / §8.1 独立提测）。

图片主色/缩略图用 Pillow（只读解码 + 像素上限）；OCR 用 rapidocr_onnxruntime、
视频关键帧用 imageio-ffmpeg（均为可选依赖，缺失时按 §2.3 降级为仅元信息 +
insight_quality 下降，绝不跳过校验、绝不把 OCR 文本当指令）。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from packages.contracts.types import JsonObject
from services.control_plane.attachments.constants import (
    MAX_KEYFRAMES,
    PROCESSOR_TIMEOUT_S,
)

if TYPE_CHECKING:
    # Pillow 为可选依赖（缺失时按 §2.3 降级），只在类型检查期导入，避免运行期强依赖
    from PIL import Image

_PIXEL_CAP = 4096
"""解码像素上限（§2.1 图片只读解码、防压缩炸弹）。"""


@dataclass(slots=True, frozen=True)
class ProcessorResult:
    """真实处理器产出（extracted + 质量 + handler 名）。"""

    handler: str
    extracted: JsonObject
    insight_quality: float


def process_visual(data: bytes, source_ref: str) -> ProcessorResult:
    """图片视觉参考：Pillow 只读解码 → 缩略图 base64 + 主色 + 尺寸。

    Args:
        data: 附件原文字节（PNG/JPEG/WebP/GIF）。
        source_ref: 原文引用（仅用于溯源，不嵌入 extracted）。

    Returns:
        ProcessorResult；像素超上限抛 ValueError（不解码压缩炸弹）。
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        if width > _PIXEL_CAP or height > _PIXEL_CAP:
            raise ValueError("INPUT_INVALID: image pixel cap exceeded")
        rgb = img.convert("RGB")
        small = rgb.copy()
        small.thumbnail((64, 64))
        palette = _dominant_colors(small)
        thumb = io.BytesIO()
        small.save(thumb, format="WEBP")
        thumb_ref = "data:image/webp;base64," + base64.b64encode(
            thumb.getvalue()
        ).decode("ascii")
    return ProcessorResult(
        "VisualRefProcessor",
        # JSON 边界收口：list[str] 因 list 不可变不能自动提升为 list[JsonValue]
        cast(
            JsonObject,
            {
                "thumbnail_ref": thumb_ref,
                "palette": palette,
                "layout_hint": f"{width}x{height}",
                "width": width,
                "height": height,
            },
        ),
        1.0,
    )


def _dominant_colors(img: Image.Image) -> list[str]:
    """从缩略图取 Top-3 主色（量化桶计数，纯代码无模型）。"""
    colors: dict[tuple[int, int, int], int] = {}
    raw = img.tobytes()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        bucket = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
        colors[bucket] = colors.get(bucket, 0) + 1
    top = sorted(colors, key=lambda c: colors[c], reverse=True)[:3]
    return ["#{:02x}{:02x}{:02x}".format(*c) for c in top]


def process_ocr(data: bytes) -> ProcessorResult:
    """内容提取：rapidocr_onnxruntime 区域化 OCR（含 bbox）。

    依赖缺失时按 §2.3 降级：extracted 置空 + insight_quality=0，
    role_confidence 不变（由服务层独立维护，不在此处触碰）。
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return ProcessorResult(
            "ContentExtractProcessor",
            {"ocr_text": "", "regions": [], "parsed_fields": [], "degraded": True},
            0.0,
        )
    engine = RapidOCR()
    result, _ = engine(data)
    regions: list[JsonObject] = []
    texts: list[str] = []
    for box, text, score in result or []:
        texts.append(str(text))
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        regions.append(
            {
                "label": "region",
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "text": str(text),
                "score": float(score),
            }
        )
    return ProcessorResult(
        "ContentExtractProcessor",
        cast(
            JsonObject,
            {"ocr_text": "\n".join(texts), "regions": regions, "parsed_fields": []},
        ),
        1.0 if regions else 0.0,
    )


def process_video(data: bytes) -> ProcessorResult:
    """视频关键帧：imageio-ffmpeg 静态二进制抽帧（不整段解码入内存）。

    依赖缺失时降级为仅元信息（§2.3），insight_quality=0。
    """
    try:
        import subprocess

        import imageio_ffmpeg
    except ImportError:
        return ProcessorResult(
            "VideoFrameProcessor",
            {"frames": [], "meta": {"size_bytes": len(data)}, "degraded": True},
            0.0,
        )
    # 从字节流抽关键帧：写临时文件 → ffmpeg -vf fps=1 -frames:v MAX_KEYFRAMES
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.mp4"
        src.write_bytes(data)
        out_pattern = Path(tmp) / "frame_%04d.jpg"
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        # 单次 ffmpeg 调用：fps 抽帧 + 帧数上限（§2.1 不整段入内存、有超时）
        proc = subprocess.run(
            [
                exe,
                "-y",
                "-i",
                str(src),
                "-vf",
                "fps=1",
                "-frames:v",
                str(MAX_KEYFRAMES),
                "-q:v",
                "5",
                str(out_pattern),
            ],
            capture_output=True,
            timeout=PROCESSOR_TIMEOUT_S,
            check=False,
        )
        frames: list[str] = []
        regions: list[JsonObject] = []
        if proc.returncode == 0:
            for idx, frame_file in enumerate(sorted(Path(tmp).glob("frame_*.jpg"))):
                raw = frame_file.read_bytes()
                frames.append(
                    "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
                )
                # A15：对每帧跑区域化 OCR，提取画面里的日期等文本区域
                try:
                    from rapidocr_onnxruntime import RapidOCR

                    ocr_result, _ = RapidOCR()(raw)
                    for box, text, score in ocr_result or []:
                        xs = [pt[0] for pt in box]
                        ys = [pt[1] for pt in box]
                        regions.append(
                            {
                                "frame_index": idx,
                                "label": "region",
                                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                                "text": str(text),
                                "score": float(score),
                            }
                        )
                except ImportError:
                    break  # OCR 可选依赖缺失 → regions 为空，降级契约生效
    return ProcessorResult(
        "VideoFrameProcessor",
        cast(
            JsonObject,
            {"frames": frames, "frame_count": len(frames), "regions": regions},
        ),
        1.0 if frames else 0.0,
    )


def process_data_source(data: bytes) -> ProcessorResult:
    """文本数据源：CSV/JSON 严格解析，值保持字符串，不 eval（§2.1）。"""
    import json

    text = data.decode("utf-8", errors="strict")
    try:
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
        return ProcessorResult(
            "DataSourceProcessor",
            cast(
                JsonObject,
                {"format": "json", "rows": rows, "row_count": len(rows)},
            ),
            1.0,
        )
    except (json.JSONDecodeError, ValueError):
        lines = [line.split(",") for line in text.splitlines() if line.strip()]
        return ProcessorResult(
            "DataSourceProcessor",
            cast(
                JsonObject,
                {
                    "format": "csv",
                    "columns": lines[0] if lines else [],
                    "rows": lines[1:] if len(lines) > 1 else [],
                    "row_count": max(len(lines) - 1, 0),
                },
            ),
            1.0,
        )
