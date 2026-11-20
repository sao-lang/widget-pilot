"""M05 附件能力 + 视觉模型接口（ADR-003 / Q6：留接口、测试 mock）测试。"""
from __future__ import annotations

from services.control_plane.attachments.vision import (
    VisionResult,
    understand_image,
)


def test_understand_image_calls_injected_gateway():
    """视觉理解复用注入的 ModelGateway，发多模态消息，返回结构化结果。"""
    calls: list[list[str]] = []

    class FakeGateway:
        def complete(self, messages, tier, stream):
            from packages.agent_core.types import ModelOutput, Usage
            from packages.contracts.enums import ModelTier

            calls.append([m.content for m in messages])
            return ModelOutput(content='{"elements": ["日历网格", "翻月按钮"]}', usage=Usage(), tier=ModelTier.STANDARD)

    result = understand_image(
        image_ref="data:image/png;base64,AAAA",
        prompt="描述界面元素",
        gateway=FakeGateway(),  # type: ignore[arg-type]
    )
    assert isinstance(result, VisionResult)
    assert result.elements == ("日历网格", "翻月按钮")
    assert len(calls) == 1
    assert "描述界面元素" in calls[0][-1]


def test_understand_image_degrades_without_gateway():
    """未注入真实模型时降级为空结果 + degraded 标记（不静默造假）。"""
    result = understand_image(image_ref="data:image/png;base64,AAAA", prompt="x", gateway=None)
    assert result.degraded is True
    assert result.elements == ()
