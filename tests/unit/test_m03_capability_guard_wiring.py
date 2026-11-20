"""ADR-003 / B2+B3：loop 内能力闸门与拒绝自纠正（不死循环）测试。"""
from __future__ import annotations

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.types import SpecState, State


def test_capability_denied_becomes_self_correction_not_crash(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """幻觉 capability_id 被闸门拒绝后转自纠正，不抛异常、不卡死。"""
    deps = Deps(
        model=fake_model,
        checkpoint_store=checkpoint_store,
        idempotency_store=idem_store,
        event_bus=event_bus,
        clock=clock,
        tracer=tracer,
        lease_provider=lease_provider,
    )
    s = State.initial("s-guard", "run-guard", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.evil"))
    checkpoint_store.states["run-guard"] = s
    # 第一轮拒绝（reason 写入），第二轮模型给 FAILED 终态收尾
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-1"}'])
    out = run("s-guard", "run-guard", "x", deps)
    assert out.terminal is not None  # 没有卡死、没有抛异常
    assert out.spec.tool_result_ref == "capability-denied/tool.evil"  # 拒绝事实入槽
    assert any("能力加载被拒" in m.content for m in fake_model.calls[-1]["messages"])  # 模型看到拒绝供自纠正


def test_capability_guard_injected_is_used(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """注入的闸门被调用（生产五项的接线证据）。"""
    calls: list[str] = []

    class Guard:
        def check(self, capability_id, level, surface, count, tokens):
            calls.append(capability_id)
            return False, "CUSTOM_DENY"

    deps = Deps(
        model=fake_model,
        checkpoint_store=checkpoint_store,
        idempotency_store=idem_store,
        event_bus=event_bus,
        clock=clock,
        tracer=tracer,
        lease_provider=lease_provider,
        capability_guard=Guard(),  # type: ignore[arg-type]
    )
    s = State.initial("s-g2", "run-g2", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.weather"))
    checkpoint_store.states["run-g2"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-1"}'])
    run("s-g2", "run-g2", "x", deps)
    assert calls == ["tool.weather"]
