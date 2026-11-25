"""ADR-014 视觉模型工具组：注册 + mock gateway 行为 + 降级。"""
from __future__ import annotations

import hashlib
import io
import json
from typing import cast

import pytest
from PIL import Image

from packages.agent_core.tools.registry import (
    REGISTRY,
    capability_detail,
    level0,
    unfreeze_registry,
)
from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.attachments.vision import understand_image
from services.control_plane.attachments.vision_tools import (
    vision_classify,
    vision_compare,
    vision_describe,
    vision_elements,
    vision_locate,
    vision_ocr,
)
from services.orchestrator.capabilities import register_capabilities

_VISION_IDS = {
    "tool.vision.describe",
    "tool.vision.elements",
    "tool.vision.locate",
    "tool.vision.ocr",
    "tool.vision.classify",
    "tool.vision.compare",
}


def _png(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (64, 48), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class MockGateway:
    """替身：按脚本返回 ModelOutput，并记录收到的 messages（不联网）。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[object]] = []

    def complete(self, messages, tier, stream):
        from packages.agent_core.types import ModelOutput, Usage
        from packages.contracts.enums import ModelTier

        self.calls.append(list(messages))
        return ModelOutput(content=self.content, usage=Usage(), tier=ModelTier.STANDARD)


class MemoryWriter:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def put_derived(self, source_key: str, name: str, data: bytes) -> str:
        key = f"derived/{source_key}/{name}"
        self.items[key] = data
        return f"{key}#sha256:{hashlib.sha256(data).hexdigest()}"


def _obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def teardown_function() -> None:
    unfreeze_registry()
    REGISTRY.clear()


def test_vision_tools_registered_and_l1_schema_real() -> None:
    unfreeze_registry()
    REGISTRY.clear()
    data = _png((1, 2, 3))
    register_capabilities(
        gateway=MockGateway('{"elements":[]}'),
        blob_reader=lambda ref: data,
        derived_writer=MemoryWriter(),
    )
    ids = {str(item["id"]) for item in level0()}
    assert _VISION_IDS <= ids
    assert len(ids) == 34  # 6 首批 + 14 媒体 + 7 CSV + 6 视觉 + 3 规程（加 weather）
    for item in level0():
        assert set(item) == {"id", "kind", "brief", "cost_hint"}
    schema = json.dumps(capability_detail("tool.vision.locate")["args_schema"])
    assert "target" in schema
    assert "source_ref_a" in json.dumps(capability_detail("tool.vision.compare")["args_schema"])


def test_understand_image_still_works_via_ask_vision() -> None:
    """旧元素接口走新通用层，行为不变。"""
    gateway = MockGateway('{"elements":["标题","网格"]}')
    result = understand_image("data:image/webp;base64,AAA", "描述", gateway)  # type: ignore[arg-type]
    assert result.degraded is False
    assert result.elements == ("标题", "网格")
    assert gateway.calls and len(gateway.calls[0]) == 1


def test_vision_degrades_without_gateway() -> None:
    data = _png((9, 9, 9))
    for result in (
        vision_describe(data, "描述", None),
        vision_elements(data, "元素", None),
        vision_locate(data, "人物", None),
        vision_ocr(data, "文字", None),
        vision_classify(data, ("a", "b"), None),
        vision_compare(data, data, "对比", None),
    ):
        assert result.degraded is True
        assert result.insight_quality == 0.0


def test_vision_locate_filters_invalid_boxes() -> None:
    gateway = MockGateway(
        json.dumps(
            {
                "elements": [
                    {"text": "人", "bbox": [10, 20, 50, 60]},
                    {"text": "坏框", "bbox": [80, 80, 10, 10]},
                    {"text": "非数", "bbox": ["x", 1, 2, 3]},
                    "纯文本元素",
                ]
            }
        )
    )
    result = vision_locate(_png((4, 5, 6)), "人", gateway)  # type: ignore[arg-type]
    assert result.degraded is False
    matches = _list(result.extracted["matches"])
    assert len(matches) == 2  # 合法 bbox 元素 + 纯文本元素
    assert _obj(matches[0])["bbox"] == [10.0, 20.0, 50.0, 60.0]
    assert result.extracted["refused_boxes"] == 2


def test_vision_classify_only_accepts_declared_labels() -> None:
    gateway = MockGateway('{"label":"日历"}')
    result = vision_classify(_png((1, 1, 1)), ("日历", "天气"), gateway)  # type: ignore[arg-type]
    assert result.extracted["label"] == "日历"

    rogue = MockGateway('{"label":"越权标签"}')
    rejected = vision_classify(_png((1, 1, 1)), ("日历", "天气"), rogue)  # type: ignore[arg-type]
    assert rejected.extracted["label"] is None
    assert rejected.degraded is False  # 有端点，只是输出不在候选内


def test_vision_elements_and_ocr_parse_elements() -> None:
    gateway = MockGateway('{"elements":["日期","按钮"]}')
    elements = vision_elements(_png((2, 2, 2)), "元素", gateway)  # type: ignore[arg-type]
    assert _list(elements.extracted["elements"]) == ["日期", "按钮"]

    ocr = vision_ocr(_png((3, 3, 3)), "文字", gateway)  # type: ignore[arg-type]
    assert _list(ocr.extracted["lines"]) == ["日期", "按钮"]
    assert ocr.extracted["raw"]


def test_vision_compare_merges_two_images_and_keeps_refs_separate() -> None:
    gateway = MockGateway("两张图的主要差异是配色")
    a, b = _png((255, 0, 0)), _png((0, 0, 255))
    result = vision_compare(a, b, "对比", gateway)  # type: ignore[arg-type]
    assert result.degraded is False
    assert "配色" in str(result.extracted["comparison"])
    assert len(gateway.calls[0]) == 1
    message = gateway.calls[0][0]
    assert getattr(message, "image_ref", "").startswith("data:image/webp;base64,")


def test_vision_handler_uses_real_gateway_wire() -> None:
    """端到端：能力 handler 走 mock gateway，输出带降级标记的 JSON。"""
    from packages.agent_core.tools.invoke import load
    from packages.agent_core.types import Decision, DecisionKind

    unfreeze_registry()
    REGISTRY.clear()
    data = _png((7, 8, 9))
    gateway = MockGateway("这是一张测试图片")
    register_capabilities(
        gateway=gateway,  # type: ignore[arg-type]
        blob_reader=lambda ref: data,
        derived_writer=MemoryWriter(),
    )
    ref = f"att/u/s/a/original#sha256:{hashlib.sha256(data).hexdigest()}"
    result = load(
        Decision(
            DecisionKind.LOAD_CAPABILITY,
            capability_id="tool.vision.describe",
            level=2,
            args={"source_ref": ref, "prompt": "这是什么"},
        )
    )
    payload = json.loads(result.output)
    assert payload["handler"] == "VisionDescribe"
    assert payload["extracted"]["description"] == "这是一张测试图片"
    assert payload["degraded"] is False
    assert gateway.calls and "这是什么" in gateway.calls[0][0].content  # type: ignore[attr-defined]


def test_vision_rejects_empty_target_and_labels() -> None:
    with pytest.raises(ValueError, match="target"):
        vision_locate(_png((1, 1, 1)), "   ", MockGateway("{}"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="labels"):
        vision_classify(_png((1, 1, 1)), (), MockGateway("{}"))  # type: ignore[arg-type]
