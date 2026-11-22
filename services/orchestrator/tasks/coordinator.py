"""ADR-006 §5 step9/§6.6 + ADR-007 Coordinator fan-in（确定性，零模型）。

轮询 + 事件驱动检查 generation_task.state；全部终态后幂等裁决会话终态，发出
唯一会话级 done（payload 带 results[]）。部分成功 = COMPLETED + notice（O3 已
拍板，不新增 PARTIAL）；全失败 = FAILED。只有 Coordinator 能发不带 taskId 的
会话级 done/error。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from packages.agent_core.types import JsonObject
from packages.contracts.enums import ReasonCode
from services.orchestrator.tasks.ports import EventEmitter
from services.orchestrator.tasks.states import TaskState, is_terminal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskResult:
    """单任务 fan-in 结果。"""

    task_id: str
    state: TaskState
    result_ref: str = ""
    reason_code: str = ""
    task_index: int = 0


@dataclass(frozen=True)
class FanInVerdict:
    """会话级裁决（Coordinator 唯一产出）。"""

    terminal: bool
    state: str
    reason_code: str = ""
    results: tuple[TaskResult, ...] = ()


def fan_in(results: tuple[TaskResult, ...], expected_total: int) -> FanInVerdict:
    """确定性 fan-in 裁决（幂等：同一批结果重复调用返回相同结论）。

    - 未全部终态 → terminal=False（不回写会话、不发 done）。
    - 全部 DONE → COMPLETED（发单条会话级 done，results[] 带 task_id → bundle）。
    - 部分成功 → COMPLETED + notice（失败 task_id + reason_code）。
    - 全失败 → FAILED（会话级 error，带 reason_code + evidence）。
    """
    if len(results) < expected_total or not all(is_terminal(r.state) for r in results):
        return FanInVerdict(False, "")
    succeeded = tuple(r for r in results if r.state is TaskState.DONE)
    if not succeeded:
        return FanInVerdict(True, "FAILED", ReasonCode.RETRY_EXHAUSTED.value, results)
    return FanInVerdict(True, "COMPLETED", "", results)


def emit_fan_in(events: EventEmitter, verdict: FanInVerdict, session_id: str) -> None:
    """发会话级事件（不带 taskId）；失败任务逐条发带 taskId 的 error/notice。"""
    if not verdict.terminal:
        return
    payload: JsonObject = {
        "sessionId": session_id,
        "results": [
            {
                "taskId": r.task_id,
                "state": r.state.value,
                "result_ref": r.result_ref,
                "reason_code": r.reason_code,
            }
            for r in verdict.results
        ]
    }
    events.emit("done" if verdict.state == "COMPLETED" else "error", payload)
    failures = tuple(r for r in verdict.results if r.state is not TaskState.DONE)
    if failures:
        notice: JsonObject = {
            "code": ReasonCode.RETRY_EXHAUSTED.value,
            "message": "部分任务未成功",
            "failed_tasks": [
                {"taskId": r.task_id, "reason_code": r.reason_code} for r in failures
            ],
        }
        events.emit("notice", notice)


__all__ = ["FanInVerdict", "TaskResult", "emit_fan_in", "fan_in"]
