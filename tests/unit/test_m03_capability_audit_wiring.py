"""ADR-003 未完成项3：loop 内能力请求留审计证据（接线测试）。"""
from __future__ import annotations

from pydantic import BaseModel

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.tools.registry import (
    REGISTRY,
    Capability,
    register,
    unfreeze_registry,
)
from packages.agent_core.types import Input, SpecState, State


class Args(BaseModel):
    x: str = "ok"


def _handler(args: Args) -> str:
    return "AUDIT-OK"


class SpyAudit:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int, str, str]] = []

    def record(self, session_id, run_id, trace_id, capability_id, level, surface, decision, latency_ms):
        self.rows.append((capability_id, level, decision, surface))


def _deps(audit, fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider):
    return Deps(
        model=fake_model, checkpoint_store=checkpoint_store, idempotency_store=idem_store,
        event_bus=event_bus, clock=clock, tracer=tracer, lease_provider=lease_provider,
        capability_audit=audit,
    )


def test_capability_load_records_audit(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """成功加载 L2 写一条审计（LOADED），含 id/level/surface。"""
    unfreeze_registry()
    REGISTRY.clear()
    register("tool.audit_probe", Capability(
        id="tool.audit_probe", kind="tool", brief="审计探针", detail_ref="args",
        payload_ref="handler", surfaces=frozenset({"generation"}), cost_hint=1,
        args_model=Args, handler=_handler,
    ))
    audit = SpyAudit()
    deps = _deps(audit, fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    s = State.initial("s-audit", "run-audit", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.audit_probe", level=2))
    checkpoint_store.states["run-audit"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev"}'])
    run("s-audit", "run-audit", Input(text="x"), deps)
    assert audit.rows and audit.rows[0][0] == "tool.audit_probe"
    assert audit.rows[0][1] == 2 and audit.rows[0][2] == "LOADED"
    unfreeze_registry()
    REGISTRY.clear()


def test_handler_valueerror_self_corrects_without_crash(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """handler 抛 ValueError（坏引用）→ run 转自纠正，不逃逸、留审计。"""
    unfreeze_registry()
    REGISTRY.clear()

    def bad_ref(args: Args) -> str:
        raise ValueError("attachment ref missing sha256 hash")

    register("tool.badref", Capability(
        id="tool.badref", kind="tool", brief="坏引用", detail_ref="args",
        payload_ref="handler", surfaces=frozenset({"generation"}), cost_hint=1,
        args_model=Args, handler=bad_ref,
    ))
    audit = SpyAudit()
    deps = _deps(audit, fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    s = State.initial("s-badref", "run-badref", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.badref", level=2))
    checkpoint_store.states["run-badref"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev"}'])
    out = run("s-badref", "run-badref", Input(text="x"), deps)
    assert out.terminal is not None  # 没逃逸、没卡死
    assert audit.rows and audit.rows[0][2] == "CONTRACT_MISMATCH"
    unfreeze_registry()
    REGISTRY.clear()


def test_capability_denied_records_audit(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """幻觉 id 被拒也写审计（DENIED），证据不丢。"""
    unfreeze_registry()
    REGISTRY.clear()
    audit = SpyAudit()
    deps = _deps(audit, fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    s = State.initial("s-audit2", "run-audit2", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.evil"))
    checkpoint_store.states["run-audit2"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev"}'])
    run("s-audit2", "run-audit2", Input(text="x"), deps)
    assert audit.rows and audit.rows[0][0] == "tool.evil"
    assert audit.rows[0][2] == "DENIED"
