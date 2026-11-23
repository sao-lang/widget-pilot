"""ADR-006/009/010 任务层内部状态机（不进 M02 契约枚举）。

任务状态机：PENDING → QUEUED → RUNNING → VALIDATING → DONE，旁路
AWAITING_INPUT / FAILED / CANCELLED。会话状态仍用 8 值 SessionState，
子任务终态不等于会话终态（ADR-006 §4.3）。

ADR-011：角色层状态机已移除，子任务直接走 run() 单 agent 内核。
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


def terminal_reason_code(state: TaskState) -> ReasonCode | None:
    """任务终态 → ReasonCode（失败类），done 返回 None。"""
    if state is TaskState.FAILED:
        return ReasonCode.RETRY_EXHAUSTED
    if state is TaskState.CANCELLED:
        return ReasonCode.HUMAN_REQUIRED
    return None
