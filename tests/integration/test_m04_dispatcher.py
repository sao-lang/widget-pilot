"""M04 dispatcher 编排回路集成测试（B1b：队列消费 + run() 驱动 + 状态回写）。

验证「提交描述 → 入队 → dispatcher 消费 → agent loop 跑 → 终态回写会话」
的链路在真实 PG/Redis 下跑通。模型用 FakeModel 注入（本机无真实 LLM，
不冒充真实模型联调）——这是接线验证，不是模型能力验证。

前置：docker compose --profile core up -d；
本机无容器时整文件 skip（连通性守卫），不冒充通过。
"""

from __future__ import annotations

import psycopg
import pytest
import redis

from packages.agent_core.types import ModelOutput, Usage
from services.control_plane.accounts.pg_stores import PgUserStore
from services.control_plane.accounts.stores import UserRow
from services.control_plane.audit.pg_ledger import PgAuditLedger
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.control_plane.sessions.service import SessionService
from services.orchestrator.deps import build_checkpoint_store
from services.orchestrator.dispatcher import Dispatcher, RedisTaskQueue
from tests.support import pass_through_validator

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
REDIS_URL = "redis://127.0.0.1:6379/0"
QUEUE_KEY = "orchestrator:tasks:test-dispatcher"


def _infra_available() -> bool:
    """探测 PG/Redis 是否可达；不可达则跳过（不冒充通过）。"""
    try:
        with psycopg.connect(PG_DSN, connect_timeout=2):
            pass
        redis.from_url(REDIS_URL, socket_connect_timeout=2).ping()
    except Exception:  # noqa: BLE001 - 连通性探测吞掉任意连接错误以决定 skip
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG/Redis 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_infra() -> None:
    """每例前清空会话/checkpoint 表与任务队列（集成测试隔离）。"""
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE users, sessions, checkpoint, audit_event CASCADE"
            )
        conn.commit()
    client = redis.from_url(REDIS_URL)
    client.delete(QUEUE_KEY)
    client.flushdb()


class TestRedisTaskQueue:
    """Redis Streams 任务队列：入队/消费/ACK。"""

    def test_enqueue_dequeue_ack(self) -> None:
        """入队后能消费到同内容，ACK 后不重复投递。"""
        queue = RedisTaskQueue(REDIS_URL, QUEUE_KEY)
        queue.enqueue({"session_id": "s1", "run_id": "r1", "description": "desc"})
        tasks = queue.consume(batch=1)
        assert len(tasks) == 1
        assert tasks[0]["run_id"] == "r1"
        queue.ack(tasks[0])
        assert queue.consume(batch=1) == []

    def test_reclaim_increments_retries_and_dead_letter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """复审 B2：PEL 认领递增重试计数并持久化，超限写死信 Stream。"""
        import services.orchestrator.dispatcher as disp

        monkeypatch.setattr(disp, "_CLAIM_IDLE_MS", 1)  # 测试中立即视为超时
        queue = RedisTaskQueue(REDIS_URL, QUEUE_KEY, consumer="reclaim-consumer")
        queue.enqueue(
            {
                "session_id": "s1",
                "user_id": "u1",
                "run_id": "r1",
                "description": "desc",
                "retries": "2",  # 已重试 2 次
            }
        )
        # consume 读新消息但不 ACK → 留 PEL
        tasks = queue.consume(batch=1)
        assert len(tasks) == 1
        assert tasks[0]["retries"] == "2"

        # 认领超时 PEL：递增到 3 并重新入队（计数持久化在 Stream）
        reclaimed = queue._reclaim(queue._conn(), batch=1)
        assert reclaimed == []  # 重新入队，走正常读取
        next_tasks = queue.consume(batch=1)
        assert len(next_tasks) == 1
        assert next_tasks[0]["retries"] == "3"  # 计数跨 Stream 往返保持（复审 B2 核心）

        # 超限写死信：原消息 + 死信 stream 都有记录
        queue.dead_letter(next_tasks[0])
        dead = redis.from_url(REDIS_URL).xlen(QUEUE_KEY + ":dead")
        assert dead == 1


class TestDispatcherE2E:
    """端到端：入队 → run() → 终态回写会话。"""

    def test_dispatch_done_writes_completed(self) -> None:
        """FakeModel 返回 DONE → run() 落 PG checkpoint → 会话 COMPLETED。"""
        from tests.conftest import (
            FakeClock,
            FakeModel,
            InMemoryEventBus,
            InMemoryIdempotencyStore,
        )

        # 真实会话（RUNNING）
        svc = SessionService(PgSessionStore(PG_DSN), audit=PgAuditLedger(PG_DSN))
        PgUserStore(PG_DSN).insert(
            UserRow(id="u1", email="u1@x.com", password_hash="x")
        )
        sid = svc.create("u1", "t", "4x4").id
        row = svc.submit_message(sid, "u1", "做一个日历", [])
        run_id = row.run_id

        # 入队
        queue = RedisTaskQueue(REDIS_URL, QUEUE_KEY)
        queue.enqueue(
            {
                "session_id": sid,
                "user_id": "u1",
                "run_id": run_id,
                "description": "做一个日历",
                "grid": "4x4",
            }
        )

        # deps：真实 checkpoint（PG+Redis），fake model/event/idem/clock
        from packages.agent_core.injection import Deps
        from services.orchestrator.adapters import PgLeaseProvider

        model = FakeModel()
        model.set_script(
            [
                ModelOutput(
                    content='{"signal":"DONE","bundle_ref":"bundle-e2e","reason":"OK"}',
                    usage=Usage(1, 1),
                )
            ]
        )
        deps = Deps(
            model=model,
            checkpoint_store=build_checkpoint_store(PG_DSN, REDIS_URL),
            idempotency_store=InMemoryIdempotencyStore(),
            event_bus=InMemoryEventBus(),
            clock=FakeClock(),
            lease_provider=PgLeaseProvider(PG_DSN),
            owner="test-worker",
        )
        dispatcher = Dispatcher(
            queue=queue,
            deps=deps,
            sessions=svc,
            validator=pass_through_validator,
        )
        processed = dispatcher.process_one()

        assert processed == 1
        assert svc.get(sid).state.value == "COMPLETED"
        # checkpoint 真实落 PG（rev 单调）
        assert run_id is not None
        assert build_checkpoint_store(PG_DSN, REDIS_URL).latest_rev(run_id) >= 1

    def test_dispatch_await_writes_awaiting_input(self) -> None:
        """review B1：模型请求澄清（ASK_USER）→ 会话转 AWAITING_INPUT，不抛 ForbiddenError。"""
        from packages.agent_core.injection import Deps
        from services.orchestrator.adapters import PgLeaseProvider
        from tests.conftest import (
            FakeClock,
            FakeModel,
            InMemoryEventBus,
            InMemoryIdempotencyStore,
        )

        svc = SessionService(PgSessionStore(PG_DSN), audit=PgAuditLedger(PG_DSN))
        PgUserStore(PG_DSN).insert(
            UserRow(id="u1", email="u1@x.com", password_hash="x")
        )
        sid = svc.create("u1", "t", "4x4").id
        row = svc.submit_message(sid, "u1", "做一个日历", [])

        queue = RedisTaskQueue(REDIS_URL, QUEUE_KEY)
        queue.enqueue(
            {
                "session_id": sid,
                "user_id": "u1",
                "run_id": row.run_id,
                "description": "做一个日历",
                "grid": "4x4",
            }
        )
        model = FakeModel()
        model.set_script(
            [
                ModelOutput(
                    content='{"signal":"ASK_USER","question":"配色？","reason":"CLARIFICATION_REQUIRED"}',
                    usage=Usage(1, 1),
                )
            ]
        )
        deps = Deps(
            model=model,
            checkpoint_store=build_checkpoint_store(PG_DSN, REDIS_URL),
            idempotency_store=InMemoryIdempotencyStore(),
            event_bus=InMemoryEventBus(),
            clock=FakeClock(),
            lease_provider=PgLeaseProvider(PG_DSN),
            owner="test-worker",
        )
        dispatcher = Dispatcher(
            queue=queue,
            deps=deps,
            sessions=svc,
            validator=pass_through_validator,
        )
        assert dispatcher.process_one() == 1
        assert svc.get(sid).state.value == "AWAITING_INPUT"
        # 审计真实落库（owner 是真实 u1，不再空串）
        assert any(
            r.action == "state_transition"
            for r in PgAuditLedger(PG_DSN).by_session(sid)
        )

    def test_dispatch_failure_does_not_ack(self) -> None:
        """review B2：run() 抛异常时不 ACK，任务留在 PEL 可重投（不静默丢）。"""
        from packages.agent_core.errors import CoreError
        from packages.agent_core.injection import Deps
        from services.orchestrator.adapters import PgLeaseProvider
        from tests.conftest import (
            FakeClock,
            FakeModel,
            InMemoryEventBus,
            InMemoryIdempotencyStore,
        )

        svc = SessionService(PgSessionStore(PG_DSN), audit=PgAuditLedger(PG_DSN))
        PgUserStore(PG_DSN).insert(
            UserRow(id="u1", email="u1@x.com", password_hash="x")
        )
        sid = svc.create("u1", "t", "4x4").id
        row = svc.submit_message(sid, "u1", "做一个日历", [])

        queue = RedisTaskQueue(REDIS_URL, QUEUE_KEY, consumer="fail-consumer")
        queue.enqueue(
            {
                "session_id": sid,
                "user_id": "u1",
                "run_id": row.run_id,
                "description": "做一个日历",
            }
        )
        model = FakeModel()
        model.set_script([CoreError.infra("MODEL_ERROR", "simulated failure")])
        deps = Deps(
            model=model,
            checkpoint_store=build_checkpoint_store(PG_DSN, REDIS_URL),
            idempotency_store=InMemoryIdempotencyStore(),
            event_bus=InMemoryEventBus(),
            clock=FakeClock(),
            lease_provider=PgLeaseProvider(PG_DSN),
            owner="test-worker",
        )
        dispatcher = Dispatcher(
            queue=queue,
            deps=deps,
            sessions=svc,
            validator=pass_through_validator,
        )
        with pytest.raises(CoreError):
            dispatcher.process_one()
        # 消息未 ACK → 仍在 PEL（pending）
        pending = redis.from_url(REDIS_URL).xpending(QUEUE_KEY, "orchestrator")
        assert pending["pending"] > 0  # PEL 中有未确认消息
