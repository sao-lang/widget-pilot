"""ADR-019 D1/D2：generation_path 随成本账落账。"""
from __future__ import annotations

from packages.agent_core.injection import Deps
from packages.agent_core.types import Decision, DecisionKind, State
from services.orchestrator.cost.meter_adapter import accrue
from services.orchestrator.cost.ports import CostEntryMeta
from tests.conftest import (
    FakeClock,
    FakeModel,
    InMemoryEventBus,
    InMemoryIdempotencyStore,
)


def _deps() -> Deps:
    return Deps(
        model=FakeModel(), checkpoint_store=None,  # type: ignore[arg-type]
        idempotency_store=InMemoryIdempotencyStore(),
        event_bus=InMemoryEventBus(), clock=FakeClock(),
        lease_provider=None,  # type: ignore[arg-type]
        owner="test",
    )


def test_accrue_persists_generation_path() -> None:
    """CostEntryMeta.generation_path 落进 CostEntry（供 cost_ledger 分组统计）。"""
    from services.orchestrator.cost.ports import CostEntry

    captured: list[CostEntry] = []

    class _Ledger:
        def append(self, entry: CostEntry) -> None:
            captured.append(entry)

    state = State.initial("s1", "r1", "做一个日历").terminate(
        Decision(DecisionKind.DONE), bundle_ref="b1"
    )
    accrue(
        state, _deps(), _Ledger(),  # type: ignore[arg-type]
        CostEntryMeta(user_id="u1", generation_path="deterministic"),
    )
    assert captured[0].generation_path == "deterministic"


def test_cost_entry_meta_default_path() -> None:
    """未注入 path 时默认 llm-only（老调用点兼容，语义=纯 LLM 路径）。"""
    meta = CostEntryMeta(user_id="u1")
    assert meta.generation_path == "llm-only"
