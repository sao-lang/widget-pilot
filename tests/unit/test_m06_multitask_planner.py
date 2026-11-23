"""ADR-006/009/010 任务层单测：规划器、fan-out、Coordinator。

模型端口用 mock（tests/support 风格，生产无真实端点）；零 LLM 判据有明确断言。
ADR-011：角色层（Router/角色循环）测试已移除。
"""
from __future__ import annotations

from packages.contracts.enums import GridSpec
from services.orchestrator.tasks.coordinator import TaskResult, fan_in
from services.orchestrator.tasks.fanout import fanout_from_plan, needs_planning
from services.orchestrator.tasks.planner import (
    PlanValidationError,
    extract_facts,
    validate_plan,
)
from services.orchestrator.tasks.ports import (
    PlanningFacts,
    TaskDraft,
    TaskPlanDraft,
)
from services.orchestrator.tasks.states import TaskState


class MockPlanLLM:
    """测试用规划端口（只在本文件定义，生产无 fake）。"""

    def __init__(self, drafts: list[TaskPlanDraft], calls: list[int] | None = None) -> None:
        self._drafts = drafts
        self.calls = calls if calls is not None else []

    def plan(self, facts: PlanningFacts, repair_errors: tuple[PlanValidationError, ...]) -> TaskPlanDraft:
        self.calls.append(1)
        return self._drafts[min(len(self.calls) - 1, len(self._drafts) - 1)]


def _facts(**kw: object) -> PlanningFacts:
    return extract_facts("s1", "parent-1", "做一个时钟和一个日历", **kw)  # type: ignore[arg-type]


def test_needs_planning_single_vs_multi():
    assert needs_planning("做一个时钟") is False
    assert needs_planning("做一个时钟和一个日历") is True
    assert needs_planning("天气、日历、倒计时三个") is True


def test_v2_rejects_hallucinated_attachment():
    draft = TaskPlanDraft(tasks=(
        TaskDraft(index=1, description="做一个时钟", attachment_ids=("ghost-id",)),
    ))
    facts = _facts()
    _plan, errors = validate_plan(draft, facts)
    assert any(e.code == "CONTRACT_MISMATCH" for e in errors)


def test_v2_rejects_dependency():
    draft = TaskPlanDraft(tasks=(
        TaskDraft(index=1, description="做一个时钟"),
        TaskDraft(index=2, description="做一个日历", rationale="先做完时钟再做这个"),
    ))
    _plan, errors = validate_plan(draft, facts=_facts())
    assert any(e.code == "CLARIFICATION_REQUIRED" for e in errors)


def test_v2_bounded_repair_called_once():
    calls: list[int] = []
    bad = TaskPlanDraft(tasks=(TaskDraft(index=1, description="x", attachment_ids=("ghost",)),))
    good = TaskPlanDraft(tasks=(TaskDraft(index=1, description="做一个时钟"),))
    llm = MockPlanLLM([bad, good], calls)
    from services.orchestrator.tasks.planner import plan
    _draft, trace, errors = plan(_facts(), llm)
    assert len(calls) == 2  # 首调 + 有界修复 1 次
    assert not errors
    assert trace.repair_count == 1


def test_fanout_task_ids_stable_and_unique():
    draft = TaskPlanDraft(tasks=(
        TaskDraft(index=1, description="做一个时钟"),
        TaskDraft(index=2, description="做一个日历", grid=GridSpec._4X4),
    ))
    result = fanout_from_plan(draft, _facts(), plan_trace_ref="trace/x", errors=())
    ids = [t.task_id for t in result.tasks]
    assert len(set(ids)) == 2
    again = fanout_from_plan(draft, _facts(), plan_trace_ref="trace/x", errors=())
    assert [t.task_id for t in again.tasks] == ids  # uuid5 幂等


# ---- Router / 黑板 / 角色循环 ----
def test_fan_in_waits_for_all_terminal():
    verdict = fan_in((TaskResult("t1", TaskState.DONE),), expected_total=2)
    assert verdict.terminal is False


def test_fan_in_partial_success_is_completed_with_notice():
    verdict = fan_in((
        TaskResult("t1", TaskState.DONE, "bundle/1"),
        TaskResult("t2", TaskState.FAILED, reason_code="RETRY_EXHAUSTED"),
    ), expected_total=2)
    assert verdict.terminal is True and verdict.state == "COMPLETED"


def test_fan_in_all_failed_is_failed():
    verdict = fan_in((
        TaskResult("t1", TaskState.FAILED),
        TaskResult("t2", TaskState.CANCELLED),
    ), expected_total=2)
    assert verdict.state == "FAILED"
