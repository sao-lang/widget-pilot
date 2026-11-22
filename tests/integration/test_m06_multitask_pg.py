"""多任务/多 Agent/多轮存储的真 PG 集成测试（迁移 0015/0016/0017）。

验证真实落库 + 幂等 + 原子抢占 + hash 链 + 乐观锁 + 并发，不是内存替身。
PG 不可达则 skip（不冒充通过）；迁移未到 head 时用例会失败（如实暴露）。

前置：docker compose --profile core up -d；alembic upgrade head。
"""

from __future__ import annotations

import concurrent.futures

import pytest

from services.orchestrator.tasks.pg_stores import (
    AgentMessageData,
    GenerationTaskData,
    PgAgentMessageStore,
    PgAgentWorkspace,
    PgGenerationTaskStore,
    PgTaskTurnStore,
    TaskTurnData,
    WorkspaceConflictError,
    _message_hash,
)
from services.orchestrator.tasks.ports import AgentMessage
from services.orchestrator.tasks.states import MessageType, Role
from tests.support.pg import pg_available, pg_connection, pg_fetchone, pg_truncate

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
_TABLES = ("generation_task", "task_turn", "agent_message", "agent_workspace")


pytestmark = pytest.mark.skipif(
    not pg_available(PG_DSN),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """每例前清空四张任务表（集成测试隔离）。"""
    with pg_connection(PG_DSN) as conn:
        pg_truncate(conn, _TABLES, restart_identity=True)


def _task(index: int = 0, *, state: str = "PENDING") -> GenerationTaskData:
    """构造一条子任务数据。"""
    return GenerationTaskData(
        id=f"task-{index}",
        session_id="s1",
        parent_run_id="parent-1",
        original_description="用户原文",
        description=f"子任务 {index}",
        task_index=index,
        task_total=2,
        state=state,
        attachment_ids=("att-1",),
    )


def _message(index: int = 0, *, session_id: str = "s1") -> AgentMessage:
    """构造一条 Router 端口消息（验证与生产端口同构）。"""
    return AgentMessage(
        session_id=session_id,
        task_id="task-0",
        agent_id=f"agent-{index}",
        from_role=Role.PLANNER,
        to_role=Role.GENERATOR,
        message_type=MessageType.TASK_ASSIGN,
        turn=index,
        trace_id=f"trace-{index}",
        payload={"note": f"go-{index}"},
    )


class TestGenerationTaskPG:
    """generation_task 真 PG 读写与并发语义。"""

    def test_create_get_list_and_idempotency(self) -> None:
        """批量落库 + 重复 create_many 不增行 + 跨实例读回。"""
        store = PgGenerationTaskStore(PG_DSN)
        store.create_many([_task(0), _task(1)])
        store.create_many([_task(0), _task(1)])  # 幂等

        fresh = PgGenerationTaskStore(PG_DSN)
        assert len(fresh.list_by_session("s1")) == 2
        got = fresh.get("task-0")
        assert got is not None
        assert got.original_description == "用户原文"
        assert got.description == "子任务 0"
        assert got.attachment_ids == ("att-1",)
        assert got.state == "PENDING"
        assert fresh.get("missing") is None

    def test_update_state_only_touches_given_fields(self) -> None:
        """只更新传入字段 + updated_at；未传字段保持原值。"""
        store = PgGenerationTaskStore(PG_DSN)
        store.create_many([_task(0)])
        store.update_state(
            "task-0",
            "DONE",
            result_ref="obj://result",
            role="reviewer",
            turn=2,
            last_turn_index=1,
            current_bundle_ref="obj://bundle",
        )
        got = store.get("task-0")
        assert got is not None
        assert got.state == "DONE"
        assert got.result_ref == "obj://result"
        assert got.role == "reviewer"
        assert got.turn == 2
        assert got.last_turn_index == 1
        assert got.current_bundle_ref == "obj://bundle"
        # 未传字段保留
        assert got.reason_code is None
        assert got.evidence_ref is None
        assert got.original_description == "用户原文"

    def test_claim_run_is_atomic(self) -> None:
        """两个 worker 抢同一任务，仅一个成功；run_id UNIQUE 不冲突。"""
        store = PgGenerationTaskStore(PG_DSN)
        store.create_many([_task(0)])
        results: list[bool] = []

        def claim(run_id: str) -> bool:
            """独立实例抢占执行权。"""
            return PgGenerationTaskStore(PG_DSN).claim_run("task-0", run_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["run-a", "run-b"]))
        assert sorted(results) == [False, True]
        got = store.get("task-0")
        assert got is not None
        assert got.run_id in {"run-a", "run-b"}

    def test_summary_for_coordinator(self) -> None:
        """会话摘要按 task_index 升序，含终态归因字段。"""
        store = PgGenerationTaskStore(PG_DSN)
        store.create_many([_task(1), _task(0)])
        store.update_state("task-0", "DONE", result_ref="obj://r0")
        summary = store.summary("s1")
        assert [row["task_index"] for row in summary] == [0, 1]
        assert summary[0]["task_id"] == "task-0"
        assert summary[0]["state"] == "DONE"
        assert summary[0]["result_ref"] == "obj://r0"
        assert store.summary("other-session") == []

    def test_asyncpg_dsn_normalized(self) -> None:
        """生产 compose 的 asyncpg DSN 归一化后仍可读写。"""
        asyncpg_dsn = PG_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
        store = PgGenerationTaskStore(asyncpg_dsn)
        store.create_many([_task(0)])
        assert PgGenerationTaskStore(PG_DSN).get("task-0") is not None


class TestTaskTurnPG:
    """task_turn 真 PG 追加与读取。"""

    def test_append_latest_and_idempotency(self) -> None:
        """重复追加同轮不增行；latest 返回最大轮次。"""
        store = PgTaskTurnStore(PG_DSN)
        store.append(TaskTurnData("task-0", 0, "parent-1", user_intent="首轮"))
        store.append(TaskTurnData("task-0", 0, "parent-1", user_intent="重复"))
        store.append(
            TaskTurnData(
                "task-0",
                1,
                "parent-1",
                user_intent="澄清",
                fact_summary_ref="obj://facts",
                result_bundle_ref="obj://bundle",
                cost_ref="obj://cost",
                state="DONE",
            )
        )
        turns = store.list_by_task("task-0")
        assert [turn.turn_index for turn in turns] == [0, 1]
        assert turns[0].user_intent == "首轮"  # 幂等跳过，未覆盖
        latest = store.latest("task-0")
        assert latest is not None
        assert latest.turn_index == 1
        assert latest.fact_summary_ref == "obj://facts"
        assert latest.result_bundle_ref == "obj://bundle"
        assert latest.cost_ref == "obj://cost"
        assert latest.state == "DONE"
        assert len(latest.id) == 36  # uuid5 派生 id
        assert store.latest("missing") is None


class TestAgentMessagePG:
    """agent_message hash 链真 PG 落库。"""

    def test_hash_chain_across_instances_and_input_shapes(self) -> None:
        """Router 端口消息与读模型都可直接 append，链可跨实例复算。"""
        store = PgAgentMessageStore(PG_DSN)
        store.append(_message(0))
        store.append(
            AgentMessageData(
                session_id="s1",
                task_id="task-0",
                agent_id="agent-1",
                from_role="generator",
                to_role="reviewer",
                message_type="REVIEW_REQUEST",
                turn=1,
                trace_id="trace-1",
                payload={"note": "please review"},
            )
        )
        messages = PgAgentMessageStore(PG_DSN).list_by_task("task-0")
        assert [m.rev for m in messages] == [1, 2]
        assert messages[0].prev_hash == "0" * 64
        assert messages[0].from_role == "planner"
        assert messages[1].prev_hash == messages[0].hash
        assert messages[1].hash == _message_hash(
            messages[0].hash, {"note": "please review"}
        )
        assert all(len(m.hash) == 64 for m in messages)

    def test_concurrent_appends_keep_chain(self) -> None:
        """并发追加不丢消息、不断链（按 session advisory lock 串行化）。"""
        store = PgAgentMessageStore(PG_DSN)

        def append(index: int) -> None:
            """并发写一条消息。"""
            store.append(_message(index, session_id="s-concurrent"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(append, range(3)))
        messages = PgAgentMessageStore(PG_DSN).list_by_task("task-0")
        assert len(messages) == 3
        assert sorted(m.rev for m in messages) == [1, 2, 3]
        for index in range(1, len(messages)):
            assert messages[index].prev_hash == messages[index - 1].hash


class TestAgentWorkspacePG:
    """agent_workspace 乐观锁真 PG 语义。"""

    def test_create_read_write_and_conflict(self) -> None:
        """首次写 → 读 → 成功写推进 rev → 旧 rev 写冲突 → 新 rev 写成功。"""
        store = PgAgentWorkspace(PG_DSN)
        key = "generator:task-0:spec"
        store.create_if_missing("s1", key, "obj://v0", "generator")
        entry = store.read("s1", key)
        assert entry is not None
        assert entry.rev == 0
        assert entry.value_ref == "obj://v0"

        store.write("s1", key, "obj://v1", "generator", 0)
        entry = store.read("s1", key)
        assert entry is not None
        assert entry.rev == 1
        assert entry.writer_role == "generator"

        with pytest.raises(WorkspaceConflictError):
            store.write("s1", key, "obj://stale", "generator", 0)
        assert store.read("s1", key).value_ref == "obj://v1"  # type: ignore[union-attr]

        store.write("s1", key, "obj://v2", "reviewer", 1)
        entry = store.read("s1", key)
        assert entry is not None
        assert entry.rev == 2
        assert entry.value_ref == "obj://v2"

    def test_create_if_missing_is_idempotent(self) -> None:
        """重复首次写不重置 rev。"""
        store = PgAgentWorkspace(PG_DSN)
        key = "review:task-0:verdict"
        store.create_if_missing("s1", key, "obj://a", "reviewer")
        store.write("s1", key, "obj://b", "reviewer", 0)
        store.create_if_missing("s1", key, "obj://a", "reviewer")
        entry = store.read("s1", key)
        assert entry is not None
        assert entry.rev == 1
        assert entry.value_ref == "obj://b"


def test_migration_columns_exist() -> None:
    """迁移 0015/0017 的归属列真实存在（guard_hit/audit_event/sessions）。"""
    with pg_connection(PG_DSN) as conn:
        for table in ("guard_hit", "audit_event", "sessions"):
            row = pg_fetchone(
                conn,
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, "bundle_ref" if table == "sessions" else "task_id"),
            )
            assert row is not None, f"{table} missing expected column"
