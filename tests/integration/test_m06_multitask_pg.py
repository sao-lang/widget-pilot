"""多任务/多轮存储的真 PG 集成测试（迁移 0015/0016/0018，ADR-011 角色层已移除）。

验证真实落库 + 幂等 + 原子抢占 + 并发，不是内存替身。
PG 不可达则 skip（不冒充通过）；迁移未到 head 时用例会失败（如实暴露）。

前置：docker compose --profile core up -d；alembic upgrade head。
"""

from __future__ import annotations

import concurrent.futures

import pytest

from services.orchestrator.tasks.pg_stores import (
    GenerationTaskData,
    PgGenerationTaskStore,
    PgTaskTurnStore,
    TaskTurnData,
)
from tests.support.pg import pg_available, pg_connection, pg_fetchone, pg_truncate

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
_TABLES = ("generation_task", "task_turn")


pytestmark = pytest.mark.skipif(
    not pg_available(PG_DSN),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """每例前清空两张任务表（集成测试隔离）。"""
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
            turn=2,
            last_turn_index=1,
            current_bundle_ref="obj://bundle",
        )
        got = store.get("task-0")
        assert got is not None
        assert got.state == "DONE"
        assert got.result_ref == "obj://result"
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
