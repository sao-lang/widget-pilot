"""M03 run() 显式 while 主循环：T3 模型隔离 / T4 循环退出 / super-step 副作用。

§11.3 逐字语义：模型只在 CALL_MODEL 分支出现；每 super-step 落盘 + emit +
budget.check + heartbeat；finally 释放租约。
"""
from __future__ import annotations

import pytest

from packages.agent_core.errors import CoreError
from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.types import ModelOutput, Usage
from packages.contracts.enums import SessionState


def make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider, **kw):
    return Deps(
        model=fake_model,
        checkpoint_store=checkpoint_store,
        idempotency_store=idem_store,
        event_bus=event_bus,
        clock=clock,
        tracer=tracer,
        lease_provider=lease_provider,
        **kw,
    )


def test_T3_model_only_called_on_call_model_branch(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """非 CALL_MODEL 分支（AWAIT_INPUT）跑一个 super-step，模型调用数 = 0。"""
    from packages.agent_core.types import SpecState, State
    s = State.initial("s-await", "run-await", "x")
    s.spec = SpecState(intent="x", signal="AWAIT_INPUT", reason="SPEC_MISSING")
    checkpoint_store.states["run-await"] = s
    deps = make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    out = run("s-await", "run-await", "x", deps)
    assert out.phase == SessionState.AWAITING_INPUT
    assert fake_model.call_count == 0


def test_run_calls_model_on_call_model_and_terminates(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """正常路径：decide=CALL_MODEL → 模型输出含 DONE signal → 终态 DONE。"""
    fake_model.set_script([
        ModelOutput(
            content='{"signal":"DONE","bundle_ref":"bundle-1"}',
            usage=Usage(input_tokens=20, output_tokens=10),
        )
    ])
    deps = make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    out = run("s-ok", "run-ok", "做一个日历", deps)
    assert out.terminal is not None
    assert out.terminal.kind == "DONE"
    assert out.terminal.bundle_ref == "bundle-1"
    assert fake_model.call_count == 1
    # 每 super-step 落盘 + emit + heartbeat
    assert checkpoint_store.latest_rev("run-ok") >= 1
    assert len(event_bus.all()) >= 1
    assert lease_provider.leases[-1].heartbeats >= 1
    assert lease_provider.leases[-1].released is True


def test_super_step_emits_checkpoint_cost_events(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    fake_model.set_script([
        ModelOutput(content='{"signal":"DONE","bundle_ref":"b"}', usage=Usage(5, 5))
    ])
    deps = make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    run("s-ev", "run-ev", "x", deps)
    kinds = [e.kind for e in event_bus.all()]
    assert "checkpoint" in kinds
    assert "cost" in kinds
    assert "done" in kinds
    # seq 连续无缺口
    seqs = [e.seq for e in event_bus.all()]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_budget_exhaustion_interrupts_deterministically(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    """四维预算触顶 → CoreError(BUDGET_EXHAUSTED)，无模型「省着点」提示。"""
    from packages.agent_core.types import State
    s = State.initial("s-budget", "run-budget", "x")
    s.budget_used = {"tokens": 999999, "iterations": 0, "tool_calls": 0, "sandbox_minutes": 0.0}
    checkpoint_store.states["run-budget"] = s
    deps = make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    with pytest.raises(CoreError) as ei:
        run("s-budget", "run-budget", "x", deps)
    assert ei.value.code == "BUDGET_EXHAUSTED"
    assert fake_model.call_count == 0


def test_lease_released_in_finally_on_model_error(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    fake_model.set_script([CoreError.infra("MODEL_ERROR", "429")])
    deps = make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    with pytest.raises(CoreError):
        run("s-err", "run-err", "x", deps)
    assert lease_provider.leases[-1].released is True
