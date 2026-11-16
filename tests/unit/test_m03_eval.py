"""M03 eval 层：T26 快照一致 GATE OK / T27 门禁只断言已有能力不退化。"""
from __future__ import annotations

from packages.agent_core.eval.gate import gate
from packages.agent_core.eval.replay import (
    build_trajectory,
    fingerprint_of,
    trajectories_equal,
)
from packages.agent_core.eval.rubric import rubric
from packages.agent_core.types import AgentEvent, EvalCase, Trajectory


def fp(**kw):
    return {"prompt_version": "p1", "recipe_version": "r1",
            "retrieval_snapshot": "snap1", "model": "m1", "model_tier": "standard", **kw}


def make_events(n=5):
    return [AgentEvent(i, "s", "run", "2026-01-01T00:00:00+00:00", "plan", "RUNNING", {"i": i}, "t")
            for i in range(n)]


# ---- T26：同题同版本两次跑轨迹一致 + GATE OK ----
def test_T26_trajectory_reproducible():
    events = make_events()
    t1 = build_trajectory(events, terminal={"kind": "DONE", "bundle_ref": "b"}, fingerprint=fp())
    t2 = build_trajectory(events, terminal={"kind": "DONE", "bundle_ref": "b"}, fingerprint=fp())
    assert trajectories_equal(t1, t2) is True
    assert fingerprint_of(t1) == fingerprint_of(t2)


def test_T26_fingerprint_change_detected():
    t1 = build_trajectory(make_events(), fingerprint=fp())
    t2 = build_trajectory(make_events(), fingerprint=fp(model="m2"))
    assert trajectories_equal(t1, t2) is False


def test_T26_gate_ok():
    case = EvalCase("case-1", fingerprint=fp())
    result = gate([case], replay_fn=lambda c: build_trajectory(make_events(), fingerprint=c.fingerprint))
    assert result.ok is True
    assert result.label == "GATE OK"


# ---- T27：门禁只断言已有能力不退化；新增能力缺失不阻塞 ----
def test_T27_gate_only_checks_no_regression():
    """有基线轨迹时：相同 → OK；评分维度退化 → FAIL。"""
    case = EvalCase("case-1", expected={"min_score": 0.8}, fingerprint=fp())
    good = Trajectory(events=[e.to_dict() for e in make_events()], fingerprint=fp())

    def replay_good(c):
        return good

    def replay_bad(c):
        return Trajectory(events=[], fingerprint=fp())  # 空轨迹 = 退化

    assert gate([case], replay_fn=replay_good, baseline={"case-1": good}).ok is True
    result = gate([case], replay_fn=replay_bad, baseline={"case-1": good})
    assert result.ok is False
    assert result.failures


def test_T27_new_capability_missing_does_not_block():
    """新增 case 无基线：缺失不阻塞（登记 backlog，不进 failure）。"""
    case_new = EvalCase("case-new", fingerprint=fp())
    result = gate([case_new], replay_fn=lambda c: Trajectory(), baseline={})
    assert result.ok is True  # 不因新增能力缺失而阻塞


# ---- rubric：失败模式加权评分 ----
def test_rubric_scores_dimensions():
    case = EvalCase("c1", expected={"must_have_done": True})
    traj = build_trajectory(make_events(), terminal={"kind": "DONE", "bundle_ref": "b"}, fingerprint=fp())
    score = rubric(case, traj)
    assert "task_success" in score.dimensions
    assert 0.0 <= score.total <= 1.0


def test_rubric_failed_terminal_low_score():
    case = EvalCase("c1")
    traj = build_trajectory(make_events(), terminal={"kind": "FAILED", "reason_code": "MODEL_ERROR"}, fingerprint=fp())
    score = rubric(case, traj)
    assert score.dimensions["task_success"] == 0.0
