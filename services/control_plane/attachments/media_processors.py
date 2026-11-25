"""ADR-014 多媒体处理纯函数（图片 / 视频 / 文案）。

设计纪律：
- 纯函数：只读原文字节 + 参数，返回结构化结果；派生字节由调用方写对象存储，
  本模块不碰 S3、不碰 run_id、不产生副作用。
- 原文红线：入参是字节（由能力层按 #sha256: 校验读取），不写日志、不内联进结果。
- ffmpeg 一律列表参数调用（无 shell=True）、单次超时、不整段解码入内存。
- 可选依赖（Pillow / imageio_ffmpeg / rapidocr）缺失时按 ADR-014 §8.3 降级，
  不伪造语义、不跳过校验。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import cast

from packages.agent_core.context.slots import INJECTION_PATTERN, INJECTION_REDACTION
from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.attachments.constants import (
    MAX_CHUNKS,
    MAX_CLIP_S,
    MAX_CROP_PADDING,
    MAX_EXTRACT_FIELDS,
    MAX_FRAME_TIMES,
    MAX_KEYFRAMES,
    MAX_TEXT_OUT,
    MAX_TEXT_ROWS,
    PROCESSOR_TIMEOUT_S,
)

_PIXEL_CAP = 4096
"""与 real_processors 一致的解码像素上限（防压缩炸弹）。"""

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_STREAM_RE = re.compile(
    r"Stream #\d+:\d+.*?Video:\s*([^,]+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+)\s*fps",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DerivedBytes:
    """派生字节 + 逻辑名（调用方据此写 derived/ 前缀并返回带 hash 的 ref）。"""

    name: str
    """逻辑文件名（如 frame_at_12.5.webp）；由本模块生成，不含用户路径。"""
    data: bytes
    """派生内容字节。"""
    mime: str
    """派生内容 MIME（供下游展示）。"""


@dataclass(frozen=True, slots=True)
class MediaResult:
    """媒体处理统一结果。"""

    handler: str
    """处理器名（审计留证）。"""
    extracted: JsonObject
    """结构化结论（可含 derived_refs 占位，由调用方填充）。"""
    derived: tuple[DerivedBytes, ...] = field(default_factory=tuple)
    """待写对象存储的派生字节；文本类工具为空。"""
    insight_quality: float = 1.0
    """结果质量；降级时下降，不为 0 以外的降级编造语义。"""
    degraded: bool = False
    """依赖缺失 / 外部故障降级标志（fail-closed 语义不造假）。"""


def _truncate_text(text: str, limit: int = MAX_TEXT_OUT) -> tuple[str, bool]:
    """超长截断并标记（不丢弃结果，保下游可判定）。"""
    return (text[:limit], True) if len(text) > limit else (text, False)


def _ffmpeg_exe() -> str:
    """取 imageio-ffmpeg 静态二进制路径；缺失抛 ImportError 由调用方降级。"""
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    """统一 ffmpeg 调用：列表参数、超时、捕获输出、不抛非零。"""
    return subprocess.run(
        args,
        capture_output=True,
        timeout=PROCESSOR_TIMEOUT_S,
        check=False,
    )


# --- 图片 ------------------------------------------------------------------


def crop_image(
    data: bytes,
    box: tuple[float, float, float, float],
    padding: int = 0,
) -> MediaResult:
    """按坐标框裁剪图片（"抠出画面里某个区域/人物"的像素级动作）。

    Args:
        data: 原文字节（能力层已按 hash 校验）。
        box: (x0, y0, x1, y1)，左上角原点；越界或非法即 ValueError（业务失败）。
        padding: 框外扩像素（受 MAX_CROP_PADDING 限制，裁到画布边界为止）。

    Returns:
        MediaResult；derived 含一张 WEBP 裁剪图。
    """
    from PIL import Image

    x0, y0, x1, y1 = box
    if not (x0 < x1 and y0 < y1):
        raise ValueError("INPUT_INVALID: crop box requires x0<x1 and y0<y1")
    if padding < 0 or padding > MAX_CROP_PADDING:
        raise ValueError("INPUT_INVALID: crop padding out of range")
    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        if width > _PIXEL_CAP or height > _PIXEL_CAP:
            raise ValueError("INPUT_INVALID: image pixel cap exceeded")
        left = max(0, int(x0) - padding)
        top = max(0, int(y0) - padding)
        right = min(width, int(x1) + padding)
        bottom = min(height, int(y1) + padding)
        if left >= right or top >= bottom:
            raise ValueError("INPUT_INVALID: crop box outside canvas")
        region = img.crop((left, top, right, bottom)).convert("RGB")
        out = io.BytesIO()
        region.save(out, format="WEBP")
        payload = out.getvalue()
    return MediaResult(
        handler="ImageCropProcessor",
        extracted=cast(
            JsonObject,
            {"box": [left, top, right, bottom], "width": right - left, "height": bottom - top},
        ),
        derived=(DerivedBytes(name="crop.webp", data=payload, mime="image/webp"),),
    )


# --- 视频 ------------------------------------------------------------------


def probe_video(data: bytes) -> MediaResult:
    """读视频元信息（时长 / 分辨率 / 帧率 / 编码），不解码整段。"""
    try:
        exe = _ffmpeg_exe()
    except ImportError:
        return MediaResult(
            "VideoProbeProcessor",
            cast(JsonObject, {"size_bytes": len(data), "degraded": True}),
            insight_quality=0.0,
            degraded=True,
        )
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.mp4"
        src.write_bytes(data)
        try:
            proc = _run_ffmpeg([exe, "-hide_banner", "-i", str(src)])
        except subprocess.TimeoutExpired:
            return MediaResult(
                "VideoProbeProcessor",
                cast(JsonObject, {"size_bytes": len(data), "degraded": True}),
                insight_quality=0.0,
                degraded=True,
            )
    stderr = proc.stderr.decode("utf-8", errors="replace")
    duration_s: float | None = None
    found = _DURATION_RE.search(stderr)
    if found is not None:
        hours, minutes, seconds = found.groups()
        duration_s = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    stream = _VIDEO_STREAM_RE.search(stderr)
    if stream is None or duration_s is None:
        return MediaResult(
            "VideoProbeProcessor",
            cast(JsonObject, {"size_bytes": len(data), "duration_s": duration_s, "degraded": True}),
            insight_quality=0.0,
            degraded=True,
        )
    codec, width, height, fps = stream.groups()
    return MediaResult(
        "VideoProbeProcessor",
        cast(
            JsonObject,
            {
                "duration_s": duration_s,
                "width": int(width),
                "height": int(height),
                "fps": float(fps),
                "codec": codec.strip(),
                "size_bytes": len(data),
            },
        ),
    )


def _extract_frame(data: bytes, at_seconds: float, fmt: str) -> tuple[bytes, str, tuple[int, int]]:
    """输入侧 seek 取单帧：ffmpeg -ss <t> -i <src> -frames:v 1。"""
    import tempfile
    from pathlib import Path

    from PIL import Image

    if at_seconds < 0:
        raise ValueError("INPUT_INVALID: at_seconds must be >= 0")
    if fmt not in ("webp", "jpg"):
        raise ValueError("INPUT_INVALID: frame format must be webp or jpg")
    exe = _ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.mp4"
        src.write_bytes(data)
        suffix = "jpg" if fmt == "jpg" else "webp"
        out = Path(tmp) / f"frame.{suffix}"
        cmd = [
            exe, "-y", "-ss", f"{at_seconds:.3f}", "-i", str(src),
            "-frames:v", "1", "-q:v", "5", str(out),
        ]
        proc = _run_ffmpeg(cmd)
        if proc.returncode != 0 or not out.exists():
            raise ValueError("PROCESSOR_FAILED: frame_at ffmpeg failed")
        payload = out.read_bytes()
        with Image.open(io.BytesIO(payload)) as frame:
            size = frame.size
    return payload, f"image/{'jpeg' if fmt == 'jpg' else 'webp'}", size


def frame_at(data: bytes, at_seconds: float, fmt: str = "webp") -> MediaResult:
    """按秒精确取一帧（ADR-014 用户点名能力）。

    返回 derived 帧字节 + 显式 at_seconds；不回传 base64 给 prompt。
    """
    payload, mime, (width, height) = _extract_frame(data, at_seconds, fmt)
    suffix = "jpg" if fmt == "jpg" else "webp"
    return MediaResult(
        handler="VideoFrameAtProcessor",
        extracted=cast(
            JsonObject,
            {"at_seconds": at_seconds, "width": width, "height": height, "mime": mime},
        ),
        derived=(DerivedBytes(name=f"frame_at_{at_seconds:.3f}.{suffix}", data=payload, mime=mime),),
    )


def keyframes(
    data: bytes, interval_s: float = 1.0, limit: int = MAX_KEYFRAMES
) -> MediaResult:
    """等间隔抽帧，每帧带显式 t_seconds（补现有 frame_index 无时间戳缺口）。"""
    if interval_s <= 0:
        raise ValueError("INPUT_INVALID: interval_s must be > 0")
    if limit <= 0 or limit > MAX_KEYFRAMES:
        raise ValueError("INPUT_INVALID: frame limit out of range")
    # 用 -ss 按时间间隔逐点取帧：fps filter 在短片/低帧率下可能只出 1 帧，
    # 输入侧 seek 的时间戳由我们显式给定，保证 t_seconds 与画面一一对应。
    derived: list[DerivedBytes] = []
    stamps: list[float] = []
    for idx in range(limit):
        at = round(idx * interval_s, 3)
        try:
            payload, _, _ = _extract_frame(data, at, "webp")
        except (ImportError, ValueError, subprocess.TimeoutExpired):
            break  # 到达片尾 / 依赖缺失 / 超时：停止，已有帧仍有效
        derived.append(
            DerivedBytes(name=f"keyframe_{idx:04d}.webp", data=payload, mime="image/webp")
        )
        stamps.append(at)
    return MediaResult(
        "VideoKeyframeProcessor",
        cast(
            JsonObject,
            {
                "frame_count": len(derived),
                "frames": [
                    cast(JsonObject, {"index": idx, "t_seconds": at})
                    for idx, at in enumerate(stamps)
                ],
            },
        ),
        derived=tuple(derived),
        insight_quality=1.0 if derived else 0.0,
        degraded=not bool(derived),
    )


def clip_video(data: bytes, start_s: float, end_s: float) -> MediaResult:
    """按时间区间导出短片段（先 stream copy，失败再重编码）。"""
    if start_s < 0 or end_s <= start_s:
        raise ValueError("INPUT_INVALID: require 0 <= start_s < end_s")
    if end_s - start_s > MAX_CLIP_S:
        raise ValueError("INPUT_INVALID: clip duration exceeds cap")
    import tempfile
    from pathlib import Path

    exe = _ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.mp4"
        src.write_bytes(data)
        out = Path(tmp) / "clip.webm"
        base = [exe, "-y", "-ss", f"{start_s:.3f}", "-i", str(src), "-t", f"{end_s - start_s:.3f}"]
        proc = _run_ffmpeg([*base, "-c", "copy", str(out)])
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            proc = _run_ffmpeg([*base, "-c:v", "libvpx-vp9", "-b:v", "1M", str(out)])
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise ValueError("PROCESSOR_FAILED: clip ffmpeg failed")
        payload = out.read_bytes()
    return MediaResult(
        "VideoClipProcessor",
        cast(JsonObject, {"start_s": start_s, "end_s": end_s, "size_bytes": len(payload)}),
        derived=(DerivedBytes(name="clip.webm", data=payload, mime="video/webm"),),
    )


def frames_ocr(data: bytes, times_s: tuple[float, ...], min_score: float = 0.5) -> MediaResult:
    """对指定时刻逐点取帧跑 OCR（结果带时间戳，恒标不可信数据）。"""
    if not times_s or len(times_s) > MAX_FRAME_TIMES:
        raise ValueError("INPUT_INVALID: times_s count out of range")
    if not 0.0 <= min_score <= 1.0:
        raise ValueError("INPUT_INVALID: min_score out of range")
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return MediaResult(
            "VideoFramesOcrProcessor",
            cast(JsonObject, {"frames": [], "degraded": True}),
            insight_quality=0.0,
            degraded=True,
        )
    engine = RapidOCR()
    frames: list[JsonObject] = []
    derived: list[DerivedBytes] = []
    for at in times_s:
        payload, _, _ = _extract_frame(data, at, "jpg")
        ocr_result, _ = engine(payload)
        regions: list[JsonObject] = []
        seen: list[str] = []
        for box, text, score in ocr_result or []:
            if float(score) < min_score:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            seen.append(str(text))
            regions.append(
                cast(
                    JsonObject,
                    {
                        "label": "region",
                        "bbox": [min(xs), min(ys), max(xs), max(ys)],
                        "text": str(text),
                        "score": float(score),
                    },
                )
            )
        frames.append(
            cast(
                JsonObject,
                {"at_seconds": at, "ocr_text": "\n".join(seen), "regions": regions},
            )
        )
        derived.append(DerivedBytes(name=f"ocr_frame_{at:.3f}.jpg", data=payload, mime="image/jpeg"))
    return MediaResult(
        "VideoFramesOcrProcessor",
        cast(JsonObject, {"frames": frames, "frame_count": len(frames)}),
        derived=tuple(derived),
        insight_quality=1.0 if any(f["regions"] for f in frames) else 0.0,
    )


# --- 文案 / 文本 ------------------------------------------------------------


def _decode_text(data: bytes) -> str:
    """严格 UTF-8 解码（带 BOM 去除由 clean 负责，此处只守编码红线）。"""
    return data.decode("utf-8", errors="strict")


def _parse_rows(text: str) -> tuple[str, list[JsonValue]]:
    """JSON 优先、CSV 兜底；值保持字符串，绝不 eval。"""
    try:
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
        return "json", [cast("JsonValue", row) for row in rows[:MAX_TEXT_ROWS]]
    except (json.JSONDecodeError, ValueError):
        lines = [line.split(",") for line in text.splitlines() if line.strip()]
        return "csv", [cast("JsonValue", line) for line in lines[:MAX_TEXT_ROWS]]


def parse_text(data: bytes, fmt: str = "auto") -> MediaResult:
    """解析 CSV/JSON 为行；fmt 指定时只走该格式，失败即业务错误。"""
    text = _decode_text(data)
    if fmt not in ("auto", "json", "csv"):
        raise ValueError("INPUT_INVALID: format must be auto/json/csv")
    got: str
    values: list[JsonValue]
    if fmt == "json":
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("INPUT_INVALID: invalid JSON") from exc
        rows = parsed if isinstance(parsed, list) else [parsed]
        got, values = "json", [cast("JsonValue", row) for row in rows[:MAX_TEXT_ROWS]]
    elif fmt == "csv":
        got, values = "csv", [
            cast("JsonValue", line.split(",")) for line in text.splitlines() if line.strip()
        ][:MAX_TEXT_ROWS]
    else:
        got, values = _parse_rows(text)
    body, truncated = _truncate_text(json.dumps(values, ensure_ascii=False))
    return MediaResult(
        "TextParseProcessor",
        cast(
            JsonObject,
            {"format": got, "row_count": len(values), "rows_preview": body, "truncated": truncated},
        ),
    )


def text_stats(data: bytes) -> MediaResult:
    """纯代码统计（字数/行数/列数/编码/BOM），语言只给启发式 tag 不下结论。"""
    text = _decode_text(data)
    lines = text.splitlines()
    has_bom = data.startswith(b"\xef\xbb\xbf")
    columns = max((len(line.split(",")) for line in lines if line.strip()), default=0)
    return MediaResult(
        "TextStatsProcessor",
        cast(
            JsonObject,
            {
                "chars": len(text),
                "lines": len(lines),
                "columns": columns,
                "encoding": "utf-8",
                "has_bom": has_bom,
            },
        ),
    )


def clean_text(data: bytes, drop_blank_lines: bool = True) -> MediaResult:
    """规范化空白并脱敏注入模式（文本恒当数据，不当指令）。"""
    text = _decode_text(data).lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    hits = len(INJECTION_PATTERN.findall(text))
    text = INJECTION_PATTERN.sub(INJECTION_REDACTION, text)
    if drop_blank_lines:
        text = "\n".join(line for line in text.split("\n") if line.strip())
    body, truncated = _truncate_text(text)
    return MediaResult(
        "TextCleanProcessor",
        cast(JsonObject, {"text": body, "redactions": hits, "truncated": truncated}),
    )


def chunk_text(data: bytes, by: str = "paragraph", size: int = 800, overlap: int = 0) -> MediaResult:
    """按段落或字符切片，确定性、有界（不 eval、不调用模型）。"""
    text = _decode_text(data).lstrip("\ufeff")
    if by not in ("paragraph", "chars"):
        raise ValueError("INPUT_INVALID: by must be paragraph or chars")
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("INPUT_INVALID: size/overlap out of range")
    segments: list[tuple[str, int]]
    if by == "paragraph":
        pieces = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        offset = 0
        segments = []
        for part in pieces:
            found = text.find(part, offset)
            segments.append((part, max(0, found)))
            offset = max(0, found) + len(part)
    else:
        segments = []
        start = 0
        while start < len(text) and len(segments) < MAX_CHUNKS:
            segments.append((text[start : start + size], start))
            start += size - overlap
    if len(segments) > MAX_CHUNKS:
        segments = segments[:MAX_CHUNKS]
    chunks = [
        cast(
            JsonObject,
            {
                "index": idx,
                "text": piece[:MAX_TEXT_OUT],
                "char_start": offset,
                "char_end": offset + len(piece),
            },
        )
        for idx, (piece, offset) in enumerate(segments)
    ]
    return MediaResult(
        "TextChunkProcessor",
        cast(JsonObject, {"by": by, "chunks": chunks, "chunk_count": len(chunks)}),
    )


def extract_fields(data: bytes, fields: tuple[str, ...]) -> MediaResult:
    """从 CSV/JSON 解析结果按字段抽取；缺字段填空串，不报错、不编造。"""
    if not fields or len(fields) > MAX_EXTRACT_FIELDS:
        raise ValueError("INPUT_INVALID: fields count out of range")
    text = _decode_text(data)
    got, rows = _parse_rows(text)
    result_rows: list[JsonObject] = []
    for row in rows:
        picked: JsonObject = {}
        for field_name in fields:
            if isinstance(row, dict):
                value = row.get(field_name, "")
            elif isinstance(row, list):
                try:
                    value = row[int(field_name)]
                except (ValueError, IndexError):
                    value = ""
            else:
                value = ""
            picked[field_name] = value if isinstance(value, str) else str(value)
        result_rows.append(picked)
        if len(result_rows) >= MAX_TEXT_ROWS:
            break
    return MediaResult(
        "TextExtractFieldsProcessor",
        cast(JsonObject, {"format": got, "rows": result_rows, "row_count": len(result_rows)}),
    )
