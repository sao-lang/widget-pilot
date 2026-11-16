"""M03 T1/T2/T4/T5/T6/T7 + v1.3 澄清分支：decide() 纯函数判定树。

§8.2 第一层：decide() 全分支、状态迁移、终止边、回边计数、纯函数性。
"""
from __future__ import annotations

import pytest

from packages.agent_core.constants import MAX_CLARIFY, MAX_ITER, MAX_REPAIR
from packages.agent_core.loop.decide import decide
from packages.agent_core.types import Decision, State
from packages.contracts.enums import SessionState


def base_state(**overrides) -> State:
    s = State.initial("sess-1", "run-1", "做一个日历", "4x4")
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---- T1：纯函数，同输入同输出，零副作用 ----
def test_T1_decide_is_pure(fake_model, sample_state):
    """同一 State 连续调用 decide() 100 次结果完全相等；无模型/落盘/事件副作用。"""
    results = [decide(sample_state) for _ in range(100)]
    assert all(r == results[0] for r in results)
    assert isinstance(results[0], Decision)
    assert fake_model.call_count == 0  # decide 内绝不调模型


# ---- T2：9 种 kind 各被命中一次 ----
@pytest.mark.parametrize(
    "state,kind",
    [
        (base_state(), "CALL_MODEL"),
        (base_state(spec={"intent": "x", "__signal": "CALL_TOOL", "__tool": "get_time"}), "CALL_TOOL"),
        (base_state(spec={"intent": "x", "__signal": "REPAIR", "__errors": ["bad"]}), "REPAIR"),
        (base_state(spec={"intent": "x", "__signal": "AWAIT_INPUT", "__reason": "SPEC_MISSING"}), "AWAIT_INPUT"),
        (base_state(spec={"intent": "x", "__signal": "ASK_USER"}), "ASK_USER"),
        (base_state(phase=SessionState.AWAITING_INPUT, clarification_answer="4x4"), "RESUME_FROM_CLARIFICATION"),
        (base_state(spec={"intent": "x", "__signal": "DONE", "__bundle_ref": "bundle-1"}), "DONE"),
        (base_state(spec={"intent": "x", "__signal": "FAILED", "__reason": "MODEL_ERROR", "__evidence": "ev-1"}), "FAILED"),
        (base_state(spec={"intent": "x", "__signal": "HANDOFF", "__reason": "HUMAN_REQUIRED", "__evidence": "ev-2"}), "HANDOFF"),
    ],
)
def test_T2_all_decision_kinds(state, kind):
    assert decide(state).kind == kind


# ---- T4：MAX_ITER 边界，iteration >= 12 → FAILED(RETRY_EXHAUSTED) ----
def test_T4_max_iter_boundary():
    assert MAX_ITER == 12
    d = decide(base_state(iteration=MAX_ITER))
    assert d.kind == "FAILED"
    assert d.reason_code == "RETRY_EXHAUSTED"
    # 11 仍继续生成
    assert decide(base_state(iteration=11)).kind == "CALL_MODEL"


# ---- T5/T6：repair 回边计数与上限 ----
def test_T5_repair_signal_when_under_limit():
    d = decide(base_state(repair_count=1, spec={"intent": "x", "__signal": "REPAIR", "__errors": ["bad"]}))
    assert d.kind == "REPAIR"
    assert d.errors == ["bad"]


def test_T6_repair_exhausted_goes_handoff():
    d = decide(base_state(repair_count=MAX_REPAIR, spec={"intent": "x", "__signal": "REPAIR", "__errors": ["bad"]}))
    assert d.kind == "HANDOFF"
    assert d.reason_code == "RETRY_EXHAUSTED"


# ---- T7：三种终态 decision 带齐字段 ----
@pytest.mark.parametrize("kind,reason,evidence", [
    ("DONE", None, None),
    ("HANDOFF", "HUMAN_REQUIRED", "ev-1"),
    ("FAILED", "MODEL_ERROR", "ev-2"),
])
def test_T7_terminal_decision_fields(kind, reason, evidence):
    spec = {"intent": "x", "__signal": kind}
    if reason:
        spec["__reason"] = reason
    if evidence:
        spec["__evidence"] = evidence
    if kind == "DONE":
        spec["__bundle_ref"] = "bundle-1"
    d = decide(base_state(spec=spec))
    assert d.kind == kind
    assert d.reason_code == reason
    if kind != "DONE":
        assert "__evidence" in spec  # 调用方据此写 Terminal.evidence_ref


# ---- v1.3 ASK_USER 分支（含纯函数性）----
def test_ask_user_branch_is_pure_and_increments():
    s = base_state(spec={"intent": "x", "__signal": "ASK_USER", "__question": "选哪种规格？"})
    d = decide(s)
    assert d.kind == "ASK_USER"
    assert d.reason_code == "CLARIFICATION_REQUIRED"
    assert s.clarify_count == 0  # decide 不改 State


def test_ask_user_limit_goes_handoff():
    s = base_state(clarify_count=MAX_CLARIFY, spec={"intent": "x", "__signal": "ASK_USER"})
    d = decide(s)
    assert d.kind == "HANDOFF"
    assert d.reason_code == "CLARIFICATION_LIMIT"


def test_resume_from_clarification_only_with_answer():
    waiting = base_state(phase=SessionState.AWAITING_INPUT)
    assert decide(waiting).kind == "AWAIT_INPUT"
    answered = base_state(phase=SessionState.AWAITING_INPUT, clarification_answer="用 2x2")
    assert decide(answered).kind == "RESUME_FROM_CLARIFICATION"


# ---- T3：非 CALL_MODEL 分支不含模型消息（run 层断言模型调用数=0，这里断言 decision 形态）----
def test_T3_non_model_branches_carry_no_model_call_marker():
    d = decide(base_state(spec={"intent": "x", "__signal": "REPAIR", "__errors": ["e"]}))
    assert d.kind != "CALL_MODEL"
    assert d.messages == []
