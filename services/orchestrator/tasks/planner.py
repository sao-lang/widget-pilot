"""ADR-006 §6.7 约束式规划器：零模型 FactExtractor + 真模型 PlanLLM + 零模型 PlanValidator。

规则负责"给事实"，模型负责"下结论"；任何越界输出由零模型 validator fail-closed
拒绝。生产无真实模型端点时 PlanLLM 端口由 worker fail-fast，不静默降级为规则规划。
"""
from __future__ import annotations

import hashlib
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from packages.agent_core.types import JsonObject, JsonValue
from packages.contracts.enums import GridSpec, ReasonCode
from services.orchestrator.tasks.ports import (
    AttachmentFact,
    PlanLLM,
    PlanningFacts,
    PlanTrace,
    PlanValidationError,
    TaskDraft,
    TaskPlanDraft,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TASKS = 3
"""ADR-006 O4 默认并发上限（硬上限 8，由配置覆盖）。"""
_HARD_MAX_TASKS = 8
_BRIEF_MAX_CHARS = 240
_REPAIR_ONCE = 1
"""有界修复次数：最多 1 次（ADR-006 §6.7）。"""


def _canonical(payload: JsonValue) -> str:
    """确定性 JSON（sort_keys + 紧凑分隔），供 hash 与 trace 复现。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(text: str) -> str:
    """UTF-8 sha256 十六进制。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def task_id_for(parent_run_id: str, index: int) -> str:
    """uuid5(parent_run_id, index) 稳定任务 id（重规划不重复落行）。"""
    return str(uuid5(NAMESPACE_URL, f"wp:task:{parent_run_id}:{index}"))


def extract_facts(
    session_id: str,
    parent_run_id: str,
    original_description: str,
    *,
    attachments: tuple[AttachmentFact, ...] = (),
    confirmed_grid: GridSpec | None = None,
    grid_source: str = "",
    budget_remaining: float = 0.0,
    max_tasks: int = _DEFAULT_MAX_TASKS,
    preferred_widget_types: tuple[str, ...] = (),
    trace_id: str = "",
) -> PlanningFacts:
    """零模型事实抽取：只收确定性字段，附件的 brief 有界截断防间接注入与超长。"""
    bounded = tuple(
        AttachmentFact(
            id=a.id,
            role=a.role,
            mime=a.mime,
            sha256=a.sha256,
            insight_ref=a.insight_ref,
            brief=a.brief[:_BRIEF_MAX_CHARS],
        )
        for a in attachments
    )
    return PlanningFacts(
        session_id=session_id,
        parent_run_id=parent_run_id,
        original_description=original_description,
        attachments=bounded,
        confirmed_grid=confirmed_grid,
        grid_source=grid_source,
        budget_remaining=budget_remaining,
        max_tasks=min(max(max_tasks, 1), _HARD_MAX_TASKS),
        preferred_widget_types=preferred_widget_types,
        trace_id=trace_id,
    )


def validate_plan(
    draft: TaskPlanDraft, facts: PlanningFacts
) -> tuple[TaskPlanDraft, tuple[PlanValidationError, ...]]:
    """V2 零模型计划级校验（fail-closed）。

    校验项：1≤N≤max_tasks / 附件 id 必须来自 facts / grid 合法或与已确认一致 /
    描述非空且无注入形状 / 任务间依赖（rationale 含"依赖/先做完"）拒绝 /
    重复任务去重。返回（规范化 plan, 错误集合）；有错时调用方不得落库与 fan-out。
    """
    errors: list[PlanValidationError] = []
    tasks = list(draft.tasks)
    if not 1 <= len(tasks) <= facts.max_tasks:
        errors.append(
            PlanValidationError("BUDGET_EXHAUSTED", "tasks", f"任务数 {len(tasks)} 超出 [1,{facts.max_tasks}]")
        )
    allowed_ids = {a.id for a in facts.attachments}
    seen_desc: set[str] = set()
    normalized: list[TaskDraft] = []
    for i, task in enumerate(tasks, start=1):
        if task.index != i:
            errors.append(PlanValidationError("INPUT_INVALID", f"tasks[{i-1}].index", f"序号应为 {i}"))
        if not task.description.strip():
            errors.append(PlanValidationError("SPEC_MISSING", f"tasks[{i-1}].description", "派生描述为空"))
        if task.description in seen_desc:
            errors.append(PlanValidationError("INPUT_INVALID", f"tasks[{i-1}].description", "重复任务"))
        seen_desc.add(task.description)
        for aid in task.attachment_ids:
            if aid not in allowed_ids:
                errors.append(
                    PlanValidationError("CONTRACT_MISMATCH", f"tasks[{i-1}].attachment_ids", f"幻觉附件 id={aid}")
                )
        try:
            grid = GridSpec(task.grid) if task.grid else None
        except ValueError:
            errors.append(PlanValidationError("INPUT_INVALID", f"tasks[{i-1}].grid", f"非法 grid={task.grid}"))
            grid = None
        if facts.confirmed_grid is not None and grid is not None and grid != facts.confirmed_grid:
            errors.append(
                PlanValidationError("SPEC_CONFLICT", f"tasks[{i-1}].grid", "与用户已确认 grid 冲突")
            )
        if any(kw in task.rationale for kw in ("依赖", "先做完", "之后再", "depends on")):
            errors.append(
                PlanValidationError("CLARIFICATION_REQUIRED", f"tasks[{i-1}].rationale", "检测到任务依赖，v1 不支持 DAG")
            )
        normalized.append(
            TaskDraft(
                index=i,
                description=task.description,
                grid=grid,
                attachment_ids=tuple(dict.fromkeys(task.attachment_ids)),
                rationale=task.rationale[:_BRIEF_MAX_CHARS],
            )
        )
    result = TaskPlanDraft(tasks=tuple(normalized), ambiguous_reason=draft.ambiguous_reason[:_BRIEF_MAX_CHARS])
    return result, tuple(errors)


def build_plan_trace(
    facts: PlanningFacts,
    draft: TaskPlanDraft,
    *,
    prompt_hash: str,
    model_id: str,
    model_version: str,
    repair_count: int,
    validator_result: str,
) -> PlanTrace:
    """产出 PlanTrace（写入 generation_task.plan_trace_ref，供复现指纹）。"""
    return PlanTrace(
        prompt_hash=prompt_hash,
        facts_hash=_sha256(_canonical(facts_to_json(facts))),
        model_id=model_id,
        model_version=model_version,
        draft_hash=_sha256(_canonical(draft_to_json(draft))),
        repair_count=repair_count,
        validator_result=validator_result,
        session_id=facts.session_id,
        parent_run_id=facts.parent_run_id,
    )


def facts_to_json(facts: PlanningFacts) -> JsonObject:
    """PlanningFacts → 可 hash 的 JSON 对象（白名单字段）。"""
    return {
        "session_id": facts.session_id,
        "parent_run_id": facts.parent_run_id,
        "description_hash": _sha256(facts.original_description),
        "attachments": [{"id": a.id, "role": a.role, "sha256": a.sha256} for a in facts.attachments],
        "confirmed_grid": facts.confirmed_grid.value if facts.confirmed_grid else None,
        "budget_remaining": facts.budget_remaining,
        "max_tasks": facts.max_tasks,
    }


def draft_to_json(draft: TaskPlanDraft) -> JsonObject:
    """TaskPlanDraft → 可 hash 的 JSON 对象。"""
    return {
        "tasks": [
            {
                "index": t.index,
                "description_hash": _sha256(t.description),
                "grid": t.grid.value if t.grid else None,
                "attachment_ids": list(t.attachment_ids),
            }
            for t in draft.tasks
        ],
        "ambiguous_reason_hash": _sha256(draft.ambiguous_reason),
    }


def plan(
    facts: PlanningFacts,
    llm: PlanLLM,
    *,
    model_id: str = "",
    model_version: str = "",
    prompt_hash: str = "",
) -> tuple[TaskPlanDraft, PlanTrace, tuple[PlanValidationError, ...]]:
    """完整规划：LLM 产出 → V2 校验 → 有界修复 1 次 → PlanTrace。

    V1 安全门禁由 dispatcher 保证在 plan() 之前（V1 命中时 llm 调用次数 = 0）。
    校验失败返回错误集合，调用方必须拒绝落库与 fan-out。
    """
    draft = llm.plan(facts, ())
    validated, errors = validate_plan(draft, facts)
    repair_count = 0
    if errors:
        repair_count = _REPAIR_ONCE
        draft = llm.plan(facts, errors)
        validated, errors = validate_plan(draft, facts)
    trace = build_plan_trace(
        facts,
        validated,
        prompt_hash=prompt_hash,
        model_id=model_id,
        model_version=model_version,
        repair_count=repair_count,
        validator_result="PASS" if not errors else "FAIL:" + ",".join(e.code for e in errors),
    )
    return validated, trace, errors


def reason_code_for_errors(errors: tuple[PlanValidationError, ...]) -> str:
    """错误集合 → 会话级 ReasonCode（取优先级最高者）。"""
    if not errors:
        return ""
    priority = (
        ReasonCode.CLARIFICATION_REQUIRED,
        ReasonCode.SPEC_CONFLICT,
        ReasonCode.CONTRACT_MISMATCH,
        ReasonCode.BUDGET_EXHAUSTED,
        ReasonCode.SPEC_MISSING,
        ReasonCode.INPUT_INVALID,
    )
    codes = {e.code for e in errors}
    for code in priority:
        if code.value in codes:
            return code.value
    return ReasonCode.INPUT_INVALID.value


__all__ = [
    "build_plan_trace",
    "draft_to_json",
    "extract_facts",
    "facts_to_json",
    "plan",
    "reason_code_for_errors",
    "task_id_for",
    "validate_plan",
]
