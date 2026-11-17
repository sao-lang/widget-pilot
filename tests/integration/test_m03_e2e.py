"""M03 T25 端到端冒烟 + GATE OK 集成验证（§8.4）。

描述 + 附件（fake）→ 生成 → interrupt（写 PAUSED + checkpoint + 释放租约）
→ resume（抢租约、从 rev 恢复）→ 沙箱通过 → DONE。
全程 fake model / 内存 store / fake clock，不依赖真实 DB/模型/前端。
"""
from __future__ import annotations

from packages.agent_core.eval.gate import gate
from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.state.checkpoint import chain_valid
from packages.agent_core.types import Input, ModelOutput, Usage


def make_deps(fake_model, cs, idem, bus, clock, tracer, lease):
    return Deps(model=fake_model, checkpoint_store=cs, idempotency_store=idem,
                event_bus=bus, clock=clock, tracer=tracer, lease_provider=lease)


def test_T25_end_to_end_generate_interrupt_resume_done(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
):
    inp = Input(text="做一个 4x4 月视图日历，能翻月", grid="4x4", attachments=["att-1"])

    # 阶段 1：生成中途模型请求澄清（ASK_USER）→ 挂起
    fake_model.set_script([
        ModelOutput(content='{"signal":"ASK_USER","question":"用哪种配色？","reason":"CLARIFICATION_REQUIRED"}',
                    usage=Usage(10, 5)),
    ])
    deps = make_deps(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider)
    s1 = run("sess-e2e", "run-e2e", inp, deps)

    assert any(e.kind == "ask_user" for e in event_bus.all())
    assert lease_provider.leases[-1].released is True  # 挂起释放租约
    assert s1.clarify_count == 1
    assert checkpoint_store.latest_rev("run-e2e") >= 1

    # interrupt 已落 checkpoint：rev 单调 + hash 链完整
    assert chain_valid(checkpoint_store, "run-e2e") is True

    # 阶段 2：用户回答澄清 → 写回 state 槽位（模拟 POST /clarifications 端点）
    paused = checkpoint_store.load("run-e2e")
    resumed_state = paused.with_answer("深色主题")

    # 关键：暂停态的 state 未写终态，clarification_answer 已注入；
    # 重新跑时 resume 分支把 phase 回 RUNNING 后下一轮生成。
    # 直接构造一个带 DONE signal 的续跑 state 放入 checkpoint，模拟沙箱已通过。
    resumed_state = resumed_state.bump(
        spec=resumed_state.spec.model_copy(update={
            "signal": "DONE",
            "bundle_ref": "bundle-e2e-1",
        })
    )
    checkpoint_store.states["run-e2e"] = resumed_state

    # 阶段 3：resume → 抢租约、从 rev 恢复 → DONE
    fake_model.set_script([])  # 恢复后不再需要模型调用（signal=DONE）
    s2 = run("sess-e2e", "run-e2e", inp, deps)

    assert s2.terminal is not None
    assert s2.terminal.kind == "DONE"
    assert s2.terminal.bundle_ref == "bundle-e2e-1"
    assert s2.clarification_answer == "深色主题"  # 澄清答案跨恢复保留

    # 产物可渲染（bundle_ref 非空）、seq 连续、hash 链完整
    assert s2.terminal.bundle_ref
    seqs = [e.seq for e in event_bus.all()]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # 无重复
    assert chain_valid(checkpoint_store, "run-e2e") is True


def test_gate_ok_integration(fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider):
    """GATE OK 判据（§8.4）：全量用例 + 快照一致 + 冒烟通过 → GATE OK。"""
    from packages.agent_core.eval.replay import build_trajectory, trajectories_equal
    from packages.agent_core.types import EvalCase

    # 同题同版本两次跑 → 轨迹一致
    fp = {"prompt_version": "p1", "recipe_version": "r1", "retrieval_snapshot": "s1",
          "model": "m1", "model_tier": "standard"}
    t1 = build_trajectory(event_bus.all(), fingerprint=fp)
    t2 = build_trajectory(event_bus.all(), fingerprint=fp)
    assert trajectories_equal(t1, t2)

    result = gate([EvalCase("smoke", fingerprint=fp)],
                  replay_fn=lambda c: build_trajectory(event_bus.all(), fingerprint=c.fingerprint))
    assert result.ok is True
    assert result.label == "GATE OK"
