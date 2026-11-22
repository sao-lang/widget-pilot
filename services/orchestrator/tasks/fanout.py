"""ADR-006 §5 step3-8 规划触发与 fan-out/fan-in 编排（确定性，零模型调度）。

设计澄清（本轮拷问结论，同步写回 ADR-006 §6.7）：
- ADR-006 §5 step3 写"每条请求入队规划任务一条"，§6.7 写"单任务零回归路径
  不经过规划器"——二者冲突。本实现采用保守触发：**单 widget 信号请求走现有
  run() 直路（零回归，真 PG/Redis 可验收）**；仅多 widget 信号请求走规划器。
  trigger 只回答"是否需要调规划器"，N 的裁定、描述派生、附件分配仍全由真实
  PlanLLM 完成（用户否决纯规则规划）。
- 规划器/Generator/Reviewer 均走真实模型端口，生产缺端点 fail-fast；测试 mock。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from packages.contracts.enums import GridSpec
from services.orchestrator.tasks.planner import (
    PlanValidationError,
    TaskPlanDraft,
    reason_code_for_errors,
    task_id_for,
)
from services.orchestrator.tasks.planner import (
    plan as run_plan,
)
from services.orchestrator.tasks.ports import PlanLLM, PlanningFacts

logger = logging.getLogger(__name__)

# 多 widget 触发信号：连接词 + 数词 + 复数指代（保守，只决定是否调规划器）
_MULTI_SIGNAL: tuple[re.Pattern[str], ...] = (
    re.compile(r"(还有|另外|以及|同时|并且|再(做|来|加)|分别|各自)"),
    re.compile(r"[两三四五六七八九十\d]+\s*(个|款|种|张)"),
    re.compile(r"(都|各)做一"),
)
_CONNECTIVE_WORDS: tuple[str, ...] = ("和", "与", "及", "、", ",")
_WIDGET_OBJECTS: tuple[str, ...] = (
    "widget", "控件", "组件", "卡片", "小组件", "桌面", "时钟", "日历",
    "闹钟", "倒计时", "天气", "待办", "清单", "便签", "任务", "计时器",
)


def needs_planning(description: str) -> bool:
    """保守判定是否需要约束式规划器（仅触发，不决定 N/描述/附件）。"""
    if any(p.search(description) for p in _MULTI_SIGNAL):
        return True
    # 连接词 + 两个以上不同控件对象词（"一个时钟和一个日历"）——对象词表仅用于
    # 触发判定，绝不参与 N 裁定/描述派生/附件分配（那些全由真实 PlanLLM 完成）。
    has_connective = any(w in description for w in _CONNECTIVE_WORDS)
    distinct_objects = {kw for kw in _WIDGET_OBJECTS if kw in description}
    return has_connective and len(distinct_objects) >= 2


@dataclass(frozen=True)
class FanoutTask:
    """fan-out 的单条子任务（落 generation_task + 入队）。"""

    task_id: str
    task_index: int
    task_total: int
    description: str
    grid: GridSpec | None
    attachment_ids: tuple[str, ...]


@dataclass(frozen=True)
class FanoutResult:
    """规划结果（dispatcher 据此决定 N=1 直路 / N>1 扇出 / 歧义挂起）。"""

    tasks: tuple[FanoutTask, ...]
    plan_trace_ref: str
    ambiguous_reason: str
    errors: tuple[PlanValidationError, ...]
    reason_code: str

    @property
    def should_fanout(self) -> bool:
        """N>1 才扇出并启用角色层；N=1 沿用现有单任务路径。"""
        return not self.errors and not self.ambiguous_reason and len(self.tasks) > 1


def fanout_from_plan(
    plan: TaskPlanDraft,
    facts: PlanningFacts,
    *,
    plan_trace_ref: str,
    errors: tuple[PlanValidationError, ...],
) -> FanoutResult:
    """TaskPlanDraft → FanoutResult（task_id 稳定，uuid5 幂等）。"""
    tasks = tuple(
        FanoutTask(
            task_id=task_id_for(facts.parent_run_id, t.index),
            task_index=t.index,
            task_total=len(plan.tasks),
            description=t.description,
            grid=t.grid,
            attachment_ids=t.attachment_ids,
        )
        for t in plan.tasks
    )
    return FanoutResult(
        tasks=tasks,
        plan_trace_ref=plan_trace_ref,
        ambiguous_reason=plan.ambiguous_reason,
        errors=errors,
        reason_code=reason_code_for_errors(errors),
    )


def run_fanout_planning(
    facts: PlanningFacts,
    llm: PlanLLM,
    *,
    model_id: str = "",
    model_version: str = "",
    prompt_hash: str = "",
) -> FanoutResult:
    """完整规划→校验→trace→fan-out 结构（dispatcher 消费后落库/入队）。"""
    draft, trace, errors = run_plan(
        facts, llm, model_id=model_id, model_version=model_version, prompt_hash=prompt_hash
    )
    trace_ref = f"plan-trace/{facts.session_id}/{facts.parent_run_id}#{trace.draft_hash}"
    return fanout_from_plan(draft, facts, plan_trace_ref=trace_ref, errors=errors)


__all__ = [
    "FanoutResult",
    "FanoutTask",
    "fanout_from_plan",
    "needs_planning",
    "run_fanout_planning",
]
