"""ADR-014 视觉模型工具组（describe / elements / locate / ocr / classify / compare）。

设计纪律：
- 一组工具共享一个 ModelGateway 多模态调用入口（vision.ask_vision），不另起 VisionPort；
- 原文由能力层按 #sha256: 校验读取后转缩略图 data URL，不把原文塞进 prompt 日志；
- 模型输出恒当不可信数据（text / bbox / label 都只作参考，不解析为指令）；
- 无真实端点时 degraded=True + insight_quality=0，不编造语义；
- bbox 数值校验（有限、非负、x1>x0、y1>y0），模型乱给即丢弃该框，fail-closed。
"""
from __future__ import annotations

import base64
import io
import math
from typing import cast

from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.attachments.media_processors import MediaResult
from services.control_plane.attachments.vision import VisionAnswer, ask_vision

_THUMB_PX = 512
"""送入视觉模型的缩略图边长（控制视觉 token 成本，原文不送）。"""

_JSON_SUFFIX = "\n只输出 JSON，不要解释、不要 Markdown 代码块。"


def _to_data_url(data: bytes) -> str:
    """原文 → WEBP 缩略图 data URL（只读解码，不让原文/大图进 wire）。"""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        small = img.convert("RGB").copy()
        small.thumbnail((_THUMB_PX, _THUMB_PX))
        buf = io.BytesIO()
        small.save(buf, format="WEBP")
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _valid_bbox(value: object) -> list[float] | None:
    """bbox 真值校验：四数、有限、非负、宽高为正；不合法返回 None。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        nums = [float(v) for v in value]
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(n) and n >= 0 for n in nums):
        return None
    if not (nums[2] > nums[0] and nums[3] > nums[1]):
        return None
    return nums


def _elements_from(answer: VisionAnswer) -> tuple[list[JsonValue], int]:
    """从模型 JSON 里取 elements（字符串或带 bbox 的对象），过滤非法框。"""
    if answer.degraded or not isinstance(answer.json_value, dict):
        return [], 0
    raw = answer.json_value.get("elements", [])
    if not isinstance(raw, list):
        return [], 0
    out: list[JsonValue] = []
    invalid = 0
    for item in raw:
        if isinstance(item, str):
            out.append(cast(JsonValue, item))
            continue
        if isinstance(item, dict):
            text = item.get("text", "")
            if not isinstance(text, str):
                continue
            box = _valid_bbox(item.get("bbox"))
            if "bbox" in item and box is None:
                invalid += 1
                continue
            out.append(cast(JsonValue, {"text": text, "bbox": box} if box else {"text": text}))
    return out, invalid


def _result(
    handler: str,
    extracted: JsonObject,
    answer: VisionAnswer,
    *,
    extra_refused: int = 0,
) -> MediaResult:
    """统一结果封装：模型原文留证（截断由 invoke.load 统一做），降级不造假。"""
    body = dict(extracted)
    body["degraded"] = answer.degraded
    if extra_refused:
        body["refused_boxes"] = extra_refused
    return MediaResult(
        handler,
        cast(JsonObject, body),
        insight_quality=0.0 if answer.degraded else 1.0,
        degraded=answer.degraded,
    )


# --- 六个工具 --------------------------------------------------------------


def vision_describe(data: bytes, prompt: str, gateway) -> MediaResult:
    """自由描述/回答：把模型文本原样返回（当数据，不解析为指令）。"""
    answer = ask_vision(_to_data_url(data), prompt or "描述这张图片的内容", gateway)
    return _result(
        "VisionDescribe",
        cast(JsonObject, {"description": answer.text, "structured": answer.json_value}),
        answer,
    )


def vision_elements(data: bytes, prompt: str, gateway) -> MediaResult:
    """元素枚举：返回带可选 bbox 的元素列表（UI 截图→控件元素）。"""
    full = (prompt or "列出图片中的所有界面元素") + '\n只输出 JSON：{"elements":[{"text":字符串,"bbox":[x0,y0,x1,y1]}]}。不要解释。'
    answer = ask_vision(_to_data_url(data), full, gateway)
    elements, refused = _elements_from(answer)
    return _result(
        "VisionElements",
        cast(JsonObject, {"elements": elements}),
        answer,
        extra_refused=refused,
    )


def vision_locate(data: bytes, target: str, gateway) -> MediaResult:
    """定位指定对象："抠出第几秒某个人物"链路中出 bbox 的那一步。"""
    if not target.strip():
        raise ValueError("INPUT_INVALID: target must not be empty")
    prompt = (
        f"在图片中定位「{target}」，只输出 JSON："
        '{"elements":[{"text":字符串,"bbox":[x0,y0,x1,y1]}]}。找不到时 elements 为空数组。不要解释。'
    )
    answer = ask_vision(_to_data_url(data), prompt, gateway)
    elements, refused = _elements_from(answer)
    return _result(
        "VisionLocate",
        cast(JsonObject, {"target": target, "matches": elements}),
        answer,
        extra_refused=refused,
    )


def vision_ocr(data: bytes, prompt: str, gateway) -> MediaResult:
    """视觉模型读文字（区别于本地 rapidocr 的 image.ocr）：返回文本行。"""
    full = (prompt or "识别图片中的所有文字") + '\n只输出 JSON：{"elements":[字符串]}。不要解释。'
    answer = ask_vision(_to_data_url(data), full, gateway)
    lines, _ = _elements_from(answer)
    return _result(
        "VisionOcr",
        cast(JsonObject, {"lines": lines, "raw": answer.text}),
        answer,
    )


def vision_classify(data: bytes, labels: tuple[str, ...], gateway) -> MediaResult:
    """从候选标签里分类：只接受候选内的标签，模型乱给一律丢弃。"""
    if not labels:
        raise ValueError("INPUT_INVALID: labels must not be empty")
    prompt = (
        "从候选标签中选择最匹配的一个，只输出 JSON：{\"label\":字符串}。候选：" + "、".join(labels) + "。不要解释。"
    )
    answer = ask_vision(_to_data_url(data), prompt, gateway)
    picked: str | None = None
    if not answer.degraded and isinstance(answer.json_value, dict):
        raw = answer.json_value.get("label")
        if isinstance(raw, str) and raw in labels:
            picked = raw
    return _result(
        "VisionClassify",
        cast(JsonObject, {"labels": list(labels), "label": picked, "raw": answer.text}),
        answer,
    )


def vision_compare(data_a: bytes, data_b: bytes, prompt: str, gateway) -> MediaResult:
    """两图对比：各自缩略图拼成一张再送模型（Message 只支持单图，不扩展契约）。"""
    left, right = _to_data_url(data_a), _to_data_url(data_b)

    def _pixels(url: str) -> bytes:
        return base64.b64decode(url.partition(",")[2])

    from PIL import Image

    with Image.open(io.BytesIO(_pixels(left))) as a, Image.open(io.BytesIO(_pixels(right))) as b:
        height = max(a.height, b.height)
        canvas = Image.new("RGB", (a.width + b.width, height), color=(255, 255, 255))
        canvas.paste(a.convert("RGB"), (0, 0))
        canvas.paste(b.convert("RGB"), (a.width, 0))
        buf = io.BytesIO()
        canvas.save(buf, format="WEBP")
    merged = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    full = (prompt or "对比这两张图片（左图与右图）的差异") + "\n左边是图 A，右边是图 B。只输出 JSON，不要解释。"
    answer = ask_vision(merged, full, gateway)
    return _result(
        "VisionCompare",
        cast(JsonObject, {"comparison": answer.text, "structured": answer.json_value}),
        answer,
    )
