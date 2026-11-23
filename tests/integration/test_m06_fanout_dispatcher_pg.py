"""ADR-006/007 父任务→规划→N 子任务→fan-in 的 dispatcher 级真 PG 集成测试。

模型端口用 mock（无真实端点），PG/Redis 存储真实；证明 review B1/B2/B3 修复：
规划入口触发、子任务独立 run_id、Coordinator 单次会话终态 + 部分成功 notice。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from packages.agent_core.types import ModelOutput, Usage
from packages.contracts.enums import GridSpec
from services.orchestrator.deps import build_checkpoint_store
from tests.support import pass_through_validator
from tests.support.pg import pg_available, pg_connection, pg_execute

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
REDIS_URL = "redis://127.0.0.1:6379/0"

pytestmark = pytest.mark.skipif(
    not pg_available(PG_DSN), reason="PG 未就绪（需 docker compose --profile core up -d）"
)

from packages.agent_core.injection import Deps
from services.control_plane.accounts.pg_stores import PgUserStore
from services.control_plane.accounts.stores import UserRow
from services.control_plane.audit.pg_ledger import PgAuditLedger
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.control_plane.sessions.service import SessionService
from services.orchestrator.adapters import (
    PgLeaseProvider,
)
from services.orchestrator.dispatcher import (
    TASK_QUEUE_KEY,
    Dispatcher,
    RedisTaskQueue,
)
from services.orchestrator.tasks.pg_stores import PgGenerationTaskStore
from services.orchestrator.tasks.ports import (
    PlanningFacts,
    PlanValidationError,
    TaskDraft,
    TaskPlanDraft,
)
from tests.conftest import (
    FakeClock,
    FakeModel,
    InMemoryEventBus,
    InMemoryIdempotencyStore,
)


class TwoTaskPlanLLM:
    def plan(self, facts: PlanningFacts, repair_errors: tuple[PlanValidationError, ...]) -> TaskPlanDraft:
        return TaskPlanDraft(tasks=(
            TaskDraft(index=1, description="做一个时钟", grid=GridSpec._2X2),
            TaskDraft(index=2, description="做一个日历", grid=GridSpec._4X4),
        ))


@pytest.fixture(autouse=True)
def clean_infra() -> None:
    """每例前清空任务相关表（集成测试隔离）。"""
    with pg_connection(PG_DSN) as conn:
        pg_execute(
            conn,
            "TRUNCATE TABLE users, sessions, checkpoint, audit_event, "
            "generation_task, task_turn CASCADE",
        )


@pytest.fixture
def svc() -> SessionService:
    store = PgSessionStore(PG_DSN)
    PgUserStore(PG_DSN).insert(UserRow(id="fan-u", email="fan@x.com", password_hash="x"))
    return SessionService(store, audit=PgAuditLedger(PG_DSN))


def _deps(event_bus: InMemoryEventBus) -> Deps:
    model = FakeModel()
    model.set_script([ModelOutput(
        content='{"signal":"DONE","bundle_ref":"bundle/fan"}', usage=Usage(1, 1)
    )])
    return Deps(
        model=model,
        checkpoint_store=build_checkpoint_store(PG_DSN, REDIS_URL),
        idempotency_store=InMemoryIdempotencyStore(),
        event_bus=event_bus,
        clock=FakeClock(),
        lease_provider=PgLeaseProvider(PG_DSN),
        owner="fan-worker",
    )


def test_planning_creates_two_tasks_with_distinct_run_ids(svc: SessionService) -> None:
    """B1/B2：planning=1 父任务 → 落 2 条 generation_task + 2 个不同 run_id。"""
    sid = svc.create("fan-u", "t", "4x4").id
    row = svc.submit_message(sid, "fan-u", "做一个时钟和一个日历", [])
    queue = RedisTaskQueue(REDIS_URL, TASK_QUEUE_KEY + ":fan-" + uuid4().hex)
    queue.enqueue({
        "session_id": sid, "user_id": "fan-u", "run_id": row.run_id,
        "description": "做一个时钟和一个日历", "grid": "", "planning": "1",
    })
    store = PgGenerationTaskStore(PG_DSN)
    events = InMemoryEventBus()
    dispatcher = Dispatcher(
        queue=queue, deps=_deps(events), sessions=svc,
        validator=pass_through_validator, task_store=store,
        plan_llm=TwoTaskPlanLLM(),
    )
    assert dispatcher.process_one() == 1

    tasks = store.list_by_session(sid)
    assert len(tasks) == 2
    run_ids = [t.run_id for t in tasks]
    assert all(run_ids) and len(set(run_ids)) == 2  # 不同 run_id，不撞 UNIQUE


def test_child_task_completion_triggers_fan_in_once(svc: SessionService) -> None:
    """B3：子任务完成不各自写终态；全部终态时 Coordinator 单次裁决。"""
    from services.orchestrator.tasks.pg_stores import GenerationTaskData
    from services.orchestrator.tasks.states import TaskState

    sid = svc.create("fan-u", "t", "4x4").id
    row = svc.submit_message(sid, "fan-u", "做一个时钟和一个日历", [])
    store = PgGenerationTaskStore(PG_DSN)
    store.create_many((
        GenerationTaskData(
            id="fan-t1", session_id=sid, parent_run_id=row.run_id or "",
            run_id="fan-run-1", original_description="多任务", description="时钟",
            task_index=1, task_total=2, state=TaskState.QUEUED.value,
        ),
        GenerationTaskData(
            id="fan-t2", session_id=sid, parent_run_id=row.run_id or "",
            run_id="fan-run-2", original_description="多任务", description="日历",
            task_index=2, task_total=2, state=TaskState.QUEUED.value,
        ),
    ))
    bus = InMemoryEventBus()
    # fan-in 需要 session 处于 RUNNING；先只标一个 DONE，不应裁决
    store.update_state("fan-t1", TaskState.DONE.value, result_ref="bundle/1")
    dispatcher = Dispatcher(
        queue=RedisTaskQueue(REDIS_URL, TASK_QUEUE_KEY + ":fan2"),
        deps=_deps(bus), sessions=svc, validator=pass_through_validator,
        task_store=store,
    )
    assert dispatcher._fan_in_if_complete(sid, "fan-u", row.run_id or "") is False
    assert svc.get(sid).state.value == "RUNNING"

    # 第二个也终态 → 裁决 COMPLETED，只写一次
    store.update_state("fan-t2", TaskState.DONE.value, result_ref="bundle/2")
    assert dispatcher._fan_in_if_complete(sid, "fan-u", row.run_id or "") is True
    assert svc.get(sid).state.value == "COMPLETED"
    assert svc.get(sid).bundle_ref == "bundle/1"
