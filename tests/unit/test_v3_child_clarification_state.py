"""P1 清理 #35：子任务 clarification 不得被误判为 FAILED 并触发 fan_in 会话失败。

问题（dispatcher.py:896-907）：子任务命中 clarification → state.terminal=None,
phase=AWAITING_INPUT，但后置链把 final 写成 FAILED → fan_in 把整会话判失败。
修复：state.phase is AWAITING_INPUT and terminal is None → 写 TaskState.AWAITING_INPUT，
is_terminal() 不含该态 → fan_in 返回 terminal=False，不裁决会话终态。

纯单测：内存 TaskStore 桩 + 确定性 runner 返回 clarification，不依赖 PG/Redis。
"""
from __future__ import annotations

from dataclasses import replace
from typing import cast

from packages.agent_core.injection import Deps
from packages.agent_core.types import JsonObject
from packages.contracts.enums import SessionState
from packages.contracts.types import TaskFields
from services.control_plane.sessions.service import SessionService
from services.guardrails.types import GridSpec, ValidationResult
from services.orchestrator.dispatcher import Dispatcher
from services.orchestrator.tasks.pg_stores import GenerationTaskData
from services.orchestrator.tasks.states import TaskState
from tests.conftest import (
    FakeClock,
    FakeLeaseProvider,
    FakeModel,
    InMemoryCheckpointStore,
    InMemoryEventBus,
    InMemoryIdempotencyStore,
)
from tests.support import AuditLedger, InMemorySessionStore


class _TaskStore:
    """最小 TaskStorePort 内存桩（只覆盖 process_one 用到的方法）。"""

    def __init__(self) -> None:
        self.rows: dict[str, GenerationTaskData] = {}
        self.updates: list[tuple[str, str]] = []

    def create_many(self, tasks) -> None:
        for t in tasks:
            self.rows[t.id] = t

    def get(self, task_id: str) -> GenerationTaskData | None:
        return self.rows.get(task_id)

    def list_by_session(self, session_id: str) -> list[GenerationTaskData]:
        return [r for r in self.rows.values() if r.session_id == session_id]

    def allocate_turn(self, task_id: str) -> int:
        return 1

    def update_state(self, task_id: str, state: str, **kwargs) -> None:
        self.updates.append((task_id, state))
        row = self.rows.get(task_id)
        if row is not None:
            self.rows[task_id] = replace(
                row, state=state,
                result_ref=kwargs.get("result_ref") or row.result_ref,
                reason_code=kwargs.get("reason_code") or row.reason_code,
            )


def _validator(description: str, fields: TaskFields) -> ValidationResult:
    return ValidationResult(ok=True, grid=GridSpec._4X4)


def _setup(child_task: bool = True):
    store = InMemorySessionStore()
    svc = SessionService(store, audit=AuditLedger())
    session = svc.create("u1", "child-clar", "4x4")
    submitted = svc.submit_message(session.id, "u1", "桌面上加个卡片", [])

    class _Queue:
        def __init__(self) -> None:
            self.acked = 0

        def consume(self, batch: int) -> list[dict[str, str]]:
            task: dict[str, str] = {
                "session_id": session.id, "user_id": "u1",
                "run_id": "child-run-1", "description": "桌面上加个卡片",
            }
            if child_task:
                task["task_id"] = "child-t1"
                task["parent_run_id"] = submitted.run_id or ""
            return [task]

        def ack(self, task: JsonObject) -> None:
            self.acked += 1

        def dead_letter(self, task: JsonObject) -> None:
            pass

    clock = FakeClock()
    deps = Deps(
        model=FakeModel(),
        checkpoint_store=InMemoryCheckpointStore(),
        idempotency_store=InMemoryIdempotencyStore(),
        event_bus=InMemoryEventBus(),
        clock=clock,
        lease_provider=FakeLeaseProvider(clock),
        owner="test",
    )
    task_store = _TaskStore()
    if child_task:
        task_store.create_many([GenerationTaskData(
            id="child-t1", session_id=session.id,
            parent_run_id=submitted.run_id or "", run_id="child-run-1",
            original_description="多任务", description="桌面上加个卡片",
            task_index=1, task_total=1, state=TaskState.QUEUED.value,
        )])
    return store, svc, session, _Queue(), deps, task_store


def _clarification_runner(inp, session_id, run_id):
    return {"branch": "clarification", "reason": "SPEC_MISSING:grid",
            "missing_slots": ["grid"], "bundle": None,
            "generation_path": "deterministic", "model_calls": 0}


def test_child_clarification_writes_awaiting_not_failed() -> None:
    """子任务 clarification → generation_task 写 AWAITING_INPUT，不是 FAILED。"""
    _store, svc, _session, queue, deps, task_store = _setup(child_task=True)
    dispatcher = Dispatcher(
        queue=cast("object", queue), deps=deps, sessions=svc,  # type: ignore[arg-type]
        validator=_validator, task_store=cast("object", task_store),  # type: ignore[arg-type]
        deterministic_runner=_clarification_runner,
    )
    assert dispatcher.process_one() == 1
    final_states = [s for tid, s in task_store.updates if tid == "child-t1"]
    assert final_states[-1] == TaskState.AWAITING_INPUT.value, task_store.updates
    child = task_store.get("child-t1")
    assert child is not None and child.state == TaskState.AWAITING_INPUT.value


def test_child_clarification_does_not_fail_session() -> None:
    """子任务挂起不应触发 fan_in 把会话判 FAILED（fan_in 应返回 terminal=False）。"""
    _store, svc, session, queue, deps, task_store = _setup(child_task=True)
    dispatcher = Dispatcher(
        queue=cast("object", queue), deps=deps, sessions=svc,  # type: ignore[arg-type]
        validator=_validator, task_store=cast("object", task_store),  # type: ignore[arg-type]
        deterministic_runner=_clarification_runner,
    )
    assert dispatcher.process_one() == 1
    assert svc.get(session.id).state is SessionState.RUNNING, \
        f"会话被误判终态: {svc.get(session.id).state}"
    assert queue.acked == 1
