"""purity-exempt-file: ADR-006/009/010 任务层真实端口协议（方法体 `...` 均为契约声明）。

生产实现由 pg_stores.py / worker.py 装配；测试注入的 Fake/Stub 只在 tests/support/。
关键边界：Planner 是**真实模型端口**，生产缺端点 fail-fast，
不得静默降级为规则规划器（ADR-006 §6.7）。角色层端口已于 ADR-011 移除。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from packages.agent_core.types import JsonObject
from packages.contracts.enums import GridSpec

if TYPE_CHECKING:
    # 仅类型检查期引用数据类（pg_stores 反向 import 本模块，运行时 import 会成环）。
    from services.orchestrator.tasks.pg_stores import GenerationTaskData, TaskTurnData


@dataclass(frozen=True)
class AttachmentFact:
    """FactExtractor 产出的附件确定性摘要（不把附件正文塞进规划 prompt）。"""

    id: str
    role: str
    mime: str
    sha256: str
    insight_ref: str
    brief: str


@dataclass(frozen=True)
class PlanningFacts:
    """零模型事实抽取结果（ADR-006 §6.7）。

    original_description 已过 V1；attachments 只含白名单字段；budget/max_tasks
    由配置注入，不由模型决定。
    """

    session_id: str
    parent_run_id: str
    original_description: str
    attachments: tuple[AttachmentFact, ...] = ()
    confirmed_grid: GridSpec | None = None
    grid_source: str = ""
    budget_remaining: float = 0.0
    max_tasks: int = 3
    preferred_widget_types: tuple[str, ...] = ()
    trace_id: str = ""


@dataclass(frozen=True)
class TaskDraft:
    """TaskPlanDraft 的单条任务（模型产出，V2 fail-closed 校验后才落库）。"""

    index: int
    description: str
    grid: GridSpec | None = None
    attachment_ids: tuple[str, ...] = ()
    rationale: str = ""


@dataclass(frozen=True)
class TaskPlanDraft:
    """PlanLLM 结构化输出（严格 JSON 解析，禁止思维链）。"""

    tasks: tuple[TaskDraft, ...] = ()
    ambiguous_reason: str = ""


@dataclass(frozen=True)
class PlanTrace:
    """规划复现留证（ADR-006 §6.7，写 generation_task.plan_trace_ref）。"""

    prompt_hash: str
    facts_hash: str
    model_id: str
    model_version: str
    draft_hash: str
    repair_count: int
    validator_result: str
    session_id: str = ""
    parent_run_id: str = ""


@dataclass(frozen=True)
class PlanValidationError:
    """V2 计划级校验的结构化错误（回灌模型做有界修复 1 次）。"""

    code: str
    field: str
    message: str


@runtime_checkable
class PlanLLM(Protocol):
    """约束式规划器真实模型端口（生产缺端点 fail-fast，测试 mock）。"""

    def plan(self, facts: PlanningFacts, repair_errors: tuple[PlanValidationError, ...]) -> TaskPlanDraft:
        """按 facts 产出严格 JSON TaskPlanDraft；用户内容在数据段非指令段。"""
        ...


@runtime_checkable
class EventEmitter(Protocol):
    """类型化事件发出口（由 dispatcher 的 deps.event_bus 实现）。"""

    def emit(
        self,
        kind: str,
        payload: JsonObject,
        *,
        task_id: str | None = None,
        task_index: int | None = None,
        turn: int | None = None,
    ) -> None:
        """发出一条带可选归因字段的事件。"""
        ...


@runtime_checkable
class TaskStorePort(Protocol):
    """generation_task 存储端口（生产 PgGenerationTaskStore；测试桩在 tests/support）。"""

    def create_many(self, tasks: Sequence[GenerationTaskData]) -> None:
        """幂等落 N 条子任务（ON CONFLICT DO NOTHING）。"""
        ...

    def get(self, task_id: str) -> GenerationTaskData | None:
        """按 task_id 读一行；不存在返回 None。"""
        ...

    def list_by_session(self, session_id: str) -> list[GenerationTaskData]:
        """会话内任务摘要（REST / 消歧用）。"""
        ...

    def allocate_turn(self, task_id: str) -> int:
        """advisory 锁原子分配下一个 turn_index（并发同任务迭代不丢轮次）。"""
        ...

    def update_state(
        self,
        task_id: str,
        state: str,
        *,
        run_id: str | None = None,
        result_ref: str | None = None,
        reason_code: str | None = None,
        evidence_ref: str | None = None,
        current_bundle_ref: str | None = None,
        last_turn_index: int | None = None,
        turn: int | None = None,
    ) -> None:
        """更新任务状态与可选字段（只更新非 None 项）。"""
        ...


@runtime_checkable
class TaskTurnStorePort(Protocol):
    """task_turn 存储端口（生产 PgTaskTurnStore）。"""

    def append(self, turn: TaskTurnData) -> None:
        """幂等追加一轮（unique(task_id, turn_index)）。"""
        ...

    def list_by_task(self, task_id: str) -> list[TaskTurnData]:
        """任务版本历史（旧版不删）。"""
        ...
