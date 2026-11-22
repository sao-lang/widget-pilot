"""purity-exempt-file: ADR-006/007/009 任务层真实端口协议（方法体 `...` 均为契约声明）。

生产实现由 pg_stores.py / worker.py 装配；测试注入的 Fake/Stub 只在 tests/support/。
关键边界：Planner/Generator/Reviewer 都是**真实模型端口**，生产缺端点 fail-fast，
不得静默降级为规则规划器（ADR-006 §6.7 / ADR-007 §1）。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from packages.agent_core.types import JsonObject, Message, ModelOutput
from packages.contracts.enums import GridSpec
from services.orchestrator.tasks.states import MessageType, Role

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


@dataclass(frozen=True)
class AgentMessage:
    """ADR-007 §4 中心路由类型化消息（payload 只含引用+枚举+结论短句）。"""

    session_id: str
    task_id: str
    agent_id: str
    from_role: Role
    to_role: Role
    message_type: MessageType
    turn: int
    trace_id: str
    payload: JsonObject = field(default_factory=dict)
    rev: int = 0


@dataclass(frozen=True)
class ReviewResult:
    """Reviewer 结构化评审结论（无代码修改权、无沙箱执行权、无终态权）。"""

    passed: bool
    reason_code: str = ""
    evidence_ref: str = ""
    artifact_ref: str = ""
    score: float = 0.0


@runtime_checkable
class PlanLLM(Protocol):
    """约束式规划器真实模型端口（生产缺端点 fail-fast，测试 mock）。"""

    def plan(self, facts: PlanningFacts, repair_errors: tuple[PlanValidationError, ...]) -> TaskPlanDraft:
        """按 facts 产出严格 JSON TaskPlanDraft；用户内容在数据段非指令段。"""
        ...


@runtime_checkable
class GeneratorPort(Protocol):
    """Generator 角色真实模型端口（按任务派生描述生成 spec/code）。"""

    def generate(self, messages: list[Message], tier: str, stream: bool) -> ModelOutput:
        """生成产物（经真实 ModelGateway）；无权裁决终态。"""
        ...


@runtime_checkable
class ReviewerPort(Protocol):
    """Reviewer 角色真实模型端口（只产出 ReviewResult，不改代码/不执行沙箱）。"""

    def review(self, messages: list[Message], tier: str, stream: bool) -> ReviewResult:
        """评审黑板产物，产出结构化结论。"""
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
        role: str | None = None,
        turn: int | None = None,
        agent_id: str | None = None,
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
        role: str | None = None,
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


@runtime_checkable
class RoleRunner(Protocol):
    """ADR-007 角色循环入口（返回 passed / artifact_ref / reason_code）。"""

    def __call__(self, session_id: str, task_id: str, description: str) -> tuple[bool, str, str]:
        """跑一轮 Generator/Reviewer 协作，不裁决会话终态。"""
        ...
