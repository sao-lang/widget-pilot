"""M05 真实多模态处理器（§2.1 技术栈 / §8.1 独立提测）。

图片主色/缩略图用 Pillow（只读解码 + 像素上限）；OCR 用 rapidocr_onnxruntime、
视频关键帧用 imageio-ffmpeg（均为可选依赖，缺失时按 §2.3 降级为仅元信息 +
insight_quality 下降，绝不跳过校验、绝不把 OCR 文本当指令）。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from services.control_plane.attachments.constants import MAX_KEYFRAMES

_PIXEL_CAP = 4096
"""解码像素上限（§2.1 图片只读解码、防压缩炸弹）。"""


@dataclass(slots=True, frozen=True)
class ProcessorResult:
    """真实处理器产出（extracted + 质量 + handler 名）。"""

    handler: str
    extracted: object
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
        {
            "thumbnail_ref": thumb_ref,
            "palette": palette,
            "layout_hint": f"{width}x{height}",
            "width": width,
            "height": height,
        },
        1.0,
    )


def _dominant_colors(img: object) -> list[str]:
    """从缩略图取 Top-3 主色（量化桶计数，纯代码无模型）。"""
    colors: dict[tuple[int, int, int], int] = {}
    raw = img.tobytes()  # type: ignore[attr-defined]
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
    regions: list[dict[str, object]] = []
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
        {"ocr_text": "\n".join(texts), "regions": regions, "parsed_fields": []},
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
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        frames: list[str] = []
        for i in range(MAX_KEYFRAMES):
            out = Path(tmp) / f"frame_{i:04d}.jpg"
            proc = subprocess.run(
                [
                    exe,
                    "-y",
                    "-i",
                    str(src),
                    "-vf",
                    f"select=eq(n\\,{i * 30})",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "5",
                    str(out),
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if proc.returncode != 0 or not out.exists():
                break
            frames.append(
                "data:image/jpeg;base64,"
                + base64.b64encode(out.read_bytes()).decode("ascii")
            )
    return ProcessorResult(
        "VideoFrameProcessor",
        {"frames": frames, "frame_count": len(frames)},
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
            {"format": "json", "rows": rows, "row_count": len(rows)},
            1.0,
        )
    except (json.JSONDecodeError, ValueError):
        lines = [line.split(",") for line in text.splitlines() if line.strip()]
        return ProcessorResult(
            "DataSourceProcessor",
            {
                "format": "csv",
                "columns": lines[0] if lines else [],
                "rows": lines[1:] if len(lines) > 1 else [],
                "row_count": max(len(lines) - 1, 0),
            },
            1.0,
        )
