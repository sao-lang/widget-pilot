"""ADR-003 未完成项3：审计适配器映射（不破坏 decisions() 口径）。"""
from __future__ import annotations

from services.guardrails.types import HitDecision
from services.orchestrator.capability_audit_adapter import ProductionCapabilityAudit


class SpyStore:
    def __init__(self) -> None:
        self.rows: list[object] = []

    def append(self, row: object) -> None:
        self.rows.append(row)


def test_adapter_maps_loaded_to_none_decision():
    """LOADED → decision=None（与旧 need_skill 通过口径一致，Counter 不炸）。"""
    audit = ProductionCapabilityAudit("postgresql://x")
    store = SpyStore()
    audit._store = store  # type: ignore[assignment]
    audit.record("s", "r", "t", "tool.x", 2, "generation", "LOADED", 5)
    row = store.rows[0]
    assert row.decision is None  # type: ignore[attr-defined]
    assert row.capability_id == "tool.x"  # type: ignore[attr-defined]
    assert row.level == 2  # type: ignore[attr-defined]


def test_adapter_maps_denied_to_reject():
    """DENIED / CAPABILITY_DENIED → decision=REJECT，reason 带 surface 与延迟。"""
    audit = ProductionCapabilityAudit("postgresql://x")
    store = SpyStore()
    audit._store = store  # type: ignore[assignment]
    audit.record("s", "r", "t", "tool.evil", 2, "generation", "DENIED", 7)
    row = store.rows[0]
    assert row.decision is HitDecision.REJECT  # type: ignore[attr-defined]
    assert "surface=generation" in row.reason  # type: ignore[attr-defined]
    assert "latency_ms=7" in row.reason  # type: ignore[attr-defined]


def test_adapter_maps_contract_mismatch_to_reject():
    """业务失败 CONTRACT_MISMATCH 也记 REJECT，不低报为通过（双口径）。"""
    audit = ProductionCapabilityAudit("postgresql://x")
    store = SpyStore()
    audit._store = store  # type: ignore[assignment]
    audit.record("s", "r", "t", "tool.bad", 2, "generation", "CONTRACT_MISMATCH", 3)
    assert store.rows[0].decision is HitDecision.REJECT  # type: ignore[attr-defined]
