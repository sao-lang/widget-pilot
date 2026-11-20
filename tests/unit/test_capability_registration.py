"""能力注册与 composition root（ADR-003 / Q2/Q4）测试。"""
from __future__ import annotations

from packages.agent_core.tools.registry import (
    REGISTRY,
    is_frozen,
    level0,
    unfreeze_registry,
)
from services.orchestrator.capabilities import register_capabilities


class FakeGateway:
    """测试替身：视觉理解返回固定 JSON，不发起真实模型调用。"""

    def complete(self, messages, tier, stream):
        from packages.agent_core.types import ModelOutput, Usage
        from packages.contracts.enums import ModelTier

        return ModelOutput(content='{"elements": ["标题", "网格"]}', usage=Usage(), tier=ModelTier.STANDARD)


def test_register_capabilities_registers_four_tools_and_two_skills():
    """首批能力：三类附件 + 代码执行（tool）+ 两个规程（skill）。"""
    unfreeze_registry()
    REGISTRY.clear()
    register_capabilities(FakeGateway())  # type: ignore[arg-type]
    ids = {str(item["id"]) for item in level0()}
    assert "tool.attachment.image" in ids
    assert "tool.attachment.ocr" in ids
    assert "tool.attachment.video" in ids
    assert "tool.code.execute" in ids
    assert "recipe.calendar.month.v3" in ids
    assert "recipe.task.list.v2" in ids
    assert is_frozen() is True
    unfreeze_registry()
    REGISTRY.clear()


def test_level0_exposes_no_schema_or_body():
    """L0 目录只摘要，不泄漏 schema/正文（FR-5）。"""
    unfreeze_registry()
    REGISTRY.clear()
    register_capabilities(FakeGateway())  # type: ignore[arg-type]
    for item in level0():
        assert set(item) == {"id", "kind", "brief", "cost_hint"}
    unfreeze_registry()
    REGISTRY.clear()
