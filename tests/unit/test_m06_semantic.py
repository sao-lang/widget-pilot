"""M06 A8/A9 + 语义层：描述充分性 / role 明确性 / 规格冲突前置。"""

from __future__ import annotations

from packages.contracts.enums import GridSpec, ReasonCode
from services.guardrails.semantic import validate_semantic
from services.guardrails.types import AttachmentView


def _att(role: str | None = None, confidence: float = 0.9) -> AttachmentView:
    """构造附件视图的测试工厂。"""
    return AttachmentView(
        attachment_id="att-1",
        role=role,
        role_confidence=confidence,
        insight_ref="insight-1",
    )


def test_a8_empty_description_awaiting() -> None:
    """A8：空描述 → INPUT_INVALID + 具体补充建议。"""
    result = validate_semantic("", [], grid=None)
    assert result.ok is False
    assert result.reason_code is ReasonCode.INPUT_INVALID
    assert len(result.suggestions) > 0


def test_short_description_awaiting() -> None:
    """描述过短 → 不足，给补充建议。"""
    result = validate_semantic("做一个", [], grid=None)
    assert result.ok is False
    assert result.reason_code is ReasonCode.INPUT_INVALID


def test_a9_role_null_not_passed() -> None:
    """A9：role=null → 不放行，请求澄清，不默认任何 role。"""
    result = validate_semantic(
        "做一个能翻月的日历，参考这张图",
        [_att(role=None)],
        grid=GridSpec._4X4,
    )
    assert result.ok is False
    assert result.reason_code is ReasonCode.INPUT_INVALID
    assert any("role" in s or "背景" in s for s in result.suggestions)


def test_low_confidence_role_not_passed() -> None:
    """role confidence 低于阈值 → 不放行。"""
    result = validate_semantic(
        "做一个能翻月的日历，参考这张图",
        [_att(role="visual_reference", confidence=0.3)],
        grid=GridSpec._4X4,
    )
    assert result.ok is False
    assert result.reason_code is ReasonCode.INPUT_INVALID


def test_spec_conflict_detected() -> None:
    """规格冲突：2x1 + 月视图 → SPEC_CONFLICT + 改选建议。"""
    result = validate_semantic(
        "做一个能翻月的日历",
        [],
        grid=GridSpec._2X1,
    )
    assert result.ok is False
    assert result.reason_code is ReasonCode.SPEC_CONFLICT
    assert result.spec_conflict is not None
    assert result.spec_conflict.suggest_grid is GridSpec._4X4


def test_valid_semantic_passed() -> None:
    """合法描述 + 明确 grid → 通过。"""
    result = validate_semantic(
        "做一个能翻月的日历",
        [],
        grid=GridSpec._4X4,
    )
    assert result.ok is True
    assert result.reason_code is None
