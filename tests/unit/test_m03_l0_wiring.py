"""ADR-003 / B1：L0 目录必须真进生产上下文（端到端，非单测 assemble）。"""
from __future__ import annotations

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.types import Input


def test_l0_catalog_reaches_model_messages(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """run() 收到 catalog 后，发给模型的 messages 含 L0 目录行（系统契约之后）。"""
    deps = Deps(
        model=fake_model,
        checkpoint_store=checkpoint_store,
        idempotency_store=idem_store,
        event_bus=event_bus,
        clock=clock,
        tracer=tracer,
        lease_provider=lease_provider,
    )
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-1"}'])
    catalog = ({"id": "tool.weather", "kind": "tool", "brief": "查天气", "cost_hint": 10},)
    run(
        "s-l0",
        "run-l0",
        Input(text="做一个日历", capability_catalog=catalog),
        deps,
    )
    assert fake_model.calls, "模型应被调用至少一次"
    contents = [m.content for m in fake_model.calls[0]["messages"]]
    assert any(c.startswith("系统契约") for c in contents)
    catalog_line = next(c for c in contents if c.startswith("可用能力"))
    assert "tool.weather" in catalog_line
