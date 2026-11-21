"""M07 偏好学习四操作与衰减领域规则测试（v1.6，ADR-002 D-10/D-11）。

RED→GREEN：先锁行为再实现。零模型调用（I-M2）、低权重不覆盖高权重（I-M3）、
高置信门禁、衰减只降权不删除、程序记忆采纳计数。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.orchestrator.memory.learning import (
    decide_operation,
    effective_weight,
    is_high_confidence,
    rank_evidence,
)
from services.orchestrator.memory.types import (
    DECAY_RECENT_FACTOR,
    DECAY_STALE_FACTOR,
    HIGH_CONFIDENCE_EVIDENCE_COUNT,
    HIGH_CONFIDENCE_WEIGHT,
    FieldEvidence,
    PreferenceDecision,
    PreferenceField,
    PreferenceSource,
)


def _now() -> str:
    """当前 UTC 秒级 ISO 串。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def test_add_on_empty_field() -> None:
    """空字段写入 → ADD。"""
    assert decide_operation(None, PreferenceSource.EXPLICIT) is PreferenceDecision.ADD


def test_low_weight_never_overwrites_high_weight() -> None:
    """I-M3：采纳信号（0.3）不能覆盖显式信号（1.0）→ NOOP。"""
    existing = FieldEvidence(
        value="4x4", source=PreferenceSource.EXPLICIT, weight=1.0,
        evidence_count=1, updated_at=_now(),
    )
    assert decide_operation(existing, PreferenceSource.ADOPTED_ARTIFACT) is PreferenceDecision.NOOP


def test_same_value_is_noop() -> None:
    """D-10：字段值无变化 → NOOP（即使来源权重更高）。"""
    existing = FieldEvidence(
        value=["4x4"], source=PreferenceSource.ADOPTED_ARTIFACT, weight=0.3,
        evidence_count=1, updated_at=_now(),
    )
    assert decide_operation(existing, PreferenceSource.EXPLICIT, ["4x4"]) is PreferenceDecision.NOOP


def test_same_weight_newer_overwrites() -> None:
    """同权重且值不同 → UPDATE（写入时裁定，取新）。"""
    existing = FieldEvidence(
        value="4x4", source=PreferenceSource.EXPLICIT, weight=1.0,
        evidence_count=1, updated_at=_now(),
    )
    assert decide_operation(existing, PreferenceSource.EXPLICIT) is PreferenceDecision.UPDATE


def test_explicit_higher_than_clarification_overwrites() -> None:
    """高权重信号（explicit 1.0）覆盖低权重既有值（clarification 0.8）→ UPDATE。"""
    existing = FieldEvidence(
        value="4x4", source=PreferenceSource.CLARIFICATION, weight=0.8,
        evidence_count=1, updated_at=_now(),
    )
    assert decide_operation(existing, PreferenceSource.EXPLICIT) is PreferenceDecision.UPDATE


def test_high_confidence_gate_weight_or_count() -> None:
    """D-11：weight≥0.7 或 evidence_count≥2 才进第 5 槽。"""
    high_weight = FieldEvidence("v", PreferenceSource.EXPLICIT, 1.0, 1, _now())
    high_count = FieldEvidence("v", PreferenceSource.CORRECTION, 0.3, 2, _now())
    low = FieldEvidence("v", PreferenceSource.ADOPTED_ARTIFACT, 0.3, 1, _now())
    assert is_high_confidence(high_weight) is True
    assert is_high_confidence(high_count) is True
    assert is_high_confidence(low) is False
    assert HIGH_CONFIDENCE_WEIGHT == 0.7
    assert HIGH_CONFIDENCE_EVIDENCE_COUNT == 2


def test_decay_recent_boost_and_stale_reduce() -> None:
    """D-11：30 天内强化 ×1.5，超 180 天 ×0.3，只影响权重不删行。"""
    recent = _now()
    stale = (datetime.now(UTC) - timedelta(days=200)).isoformat(timespec="seconds")
    assert effective_weight(0.5, recent, now=recent) == 0.5 * DECAY_RECENT_FACTOR
    assert effective_weight(0.5, stale, now=recent) == 0.5 * DECAY_STALE_FACTOR


def test_rank_by_effective_weight_desc() -> None:
    """注入排序按生效权重降序，doc_id 打破平局（确定性）。"""
    now = _now()
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat(timespec="seconds")
    items = [
        (PreferenceField.PALETTE, FieldEvidence("a", PreferenceSource.EXPLICIT, 1.0, 1, old)),
        (PreferenceField.PREFERRED_GRIDS, FieldEvidence("g", PreferenceSource.EXPLICIT, 1.0, 1, now)),
    ]
    ranked = rank_evidence(items, now=now)
    assert ranked[0][0] is PreferenceField.PREFERRED_GRIDS
