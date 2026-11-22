"""ADR-006/007/009 任务层与角色层内部状态机（不进 M02 契约枚举）。

任务状态机：PENDING → QUEUED → RUNNING → VALIDATING → DONE，旁路
AWAITING_INPUT / FAILED / CANCELLED。会话状态仍用 8 值 SessionState，
子任务终态不等于会话终态（ADR-006 §4.3）。

角色层状态：PLANNING / EXECUTING / REVIEWING / REVISING / ESCALATED 存黑板，
不进契约（ADR-007 §6）。
"""
from __future__ import annotations

from enum import StrEnum

from packages.contracts.enums import ReasonCode


class TaskState(StrEnum):
    """generation_task.state 值域（ADR-006 §4.3）。"""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    DONE = "DONE"
    AWAITING_INPUT = "AWAITING_INPUT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_TERMINAL: frozenset[TaskState] = frozenset(
    {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}
)


def is_terminal(state: str | TaskState) -> bool:
    """判断是否任务终态（Coordinator fan-in 用）。"""
    return TaskState(state) in _TERMINAL


class AgentSessionState(StrEnum):
    """角色层内部状态（ADR-007 §6，仅存 agent_workspace 不进契约）。"""

    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    REVISING = "REVISING"
    ESCALATED = "ESCALATED"


class MessageType(StrEnum):
    """ADR-007 §4 中心路由有界消息类型（v1 六种，禁自由文本）。"""

    TASK_ASSIGN = "TASK_ASSIGN"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    REVIEW_RESULT = "REVIEW_RESULT"
    REVISION_REQUEST = "REVISION_REQUEST"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    FINAL = "FINAL"


class Role(StrEnum):
    """ADR-007 §3 功能角色（不按 widget_type 分化）。"""

    PLANNER = "planner"
    GENERATOR = "generator"
    REVIEWER = "reviewer"
    COORDINATOR = "coordinator"
    """Router/Coordinator 不是 agent（零模型），仅作身份标识。"""


# 预算与终止常量（ADR-007 §7；进 enums.source.json $constants 由 P0 完成）
MAX_AGENT_TURNS = 6
MAX_REVIEW_ROUNDS = 2
MAX_DELEGATE_DEPTH = 1


def terminal_reason_code(state: TaskState) -> ReasonCode | None:
    """任务终态 → ReasonCode（失败类），done 返回 None。"""
    if state is TaskState.FAILED:
        return ReasonCode.RETRY_EXHAUSTED
    if state is TaskState.CANCELLED:
        return ReasonCode.HUMAN_REQUIRED
    return None
