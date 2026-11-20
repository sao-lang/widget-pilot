"""M04 真实基础设施集成测试（PG + Redis，§8.2 A3/A4/A5/A10/A11）。

补充 unit 层内存桩无法覆盖的部分：
- A4：PG 原子抢租约（UPDATE ... WHERE lease_until < now()），并发恰好一个赢家
- A3：租约过期后另一 worker 接管续跑（真实 SQL 时间戳判定）
- A10：PG checkpoint 落盘 rev 单调 + hash 链可重算
- A11：Redis Streams XADD/XRANGE 续传 + XTRIM 窗口
- A5：Redis SETNX 幂等占位（命中复用）

前置：docker compose --profile full up -d（PG:5432 / Redis:6379）；
本机无容器时整文件 skip（pytest.importorskip + 连通性守卫），不冒充通过。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
import redis

from packages.agent_core.errors import CoreError
from packages.agent_core.types import CheckpointNode
from packages.contracts.enums import SessionState
from services.control_plane.accounts.pg_stores import PgUserStore
from services.control_plane.accounts.service import AuthService
from services.control_plane.accounts.stores import DuplicateEmailError, UserRow
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.control_plane.sessions.service import SessionService
from services.control_plane.sessions.stores import SessionRow
from services.orchestrator.adapters import (
    PgCheckpointStore,
    PgLeaseProvider,
    RedisEventBus,
    RedisIdempotencyStore,
)
from tests.support import AuditLedger

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
REDIS_URL = "redis://127.0.0.1:6379/0"


def _infra_available() -> bool:
    """探测 PG/Redis 是否可达；不可达则跳过集成测试（不冒充通过）。"""
    try:
        with psycopg.connect(PG_DSN, connect_timeout=2):
            pass
        redis.from_url(REDIS_URL, socket_connect_timeout=2).ping()
    except Exception:  # noqa: BLE001 - 连通性探测必须吞掉任意连接错误以决定 skip
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG/Redis 未就绪（需 docker compose --profile full up -d）",
)


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """每个用例前清空 M04 业务表（集成测试隔离，避免跨用例污染）。"""
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            for table in (
                "checkpoint",
                "side_effect",
                "audit_event",
                "sessions",
                "users",
            ):
                cur.execute(f"TRUNCATE TABLE {table} CASCADE")
        conn.commit()
    redis.from_url(REDIS_URL).flushdb()


class TestPgLease:
    """A4/A3：真实 PG 原子抢租约与过期接管。"""

    def test_atomic_acquire_single_winner(self) -> None:
        """两 worker 并发抢同一 run：恰好 1 个成功（A4）。"""
        provider = PgLeaseProvider(PG_DSN)
        run_id = f"run-{uuid4()}"
        lease_a = provider.acquire(run_id, "worker-a", ttl_s=30)
        assert lease_a is not None
        # 第二抢锁者被 UPDATE ... WHERE 挡回，抛业务冲突而非返回 None
        with pytest.raises(CoreError) as ei:
            provider.acquire(run_id, "worker-b", ttl_s=30)
        assert ei.value.code == "HUMAN_REQUIRED"

    def test_lease_survives_checkpoint_save(self) -> None:
        """B2 回归：持锁 worker 写新 rev 后，第二 worker 不能抢占（单持锁者贯穿 super-step）。

        拷问实测：修复前 save() 插入的新 rev 行 lease_owner 为 NULL，
        acquire 锁在 MAX(rev) 行上 → 新行可被二次抢锁（双 worker 并发）。
        """
        provider = PgLeaseProvider(PG_DSN)
        run_id = f"run-{uuid4()}"
        lease_a = provider.acquire(run_id, "worker-a", ttl_s=30)
        assert lease_a is not None
        store = PgCheckpointStore(PG_DSN, object_get=lambda ref: b"{}")
        # 持锁期间写两个新 checkpoint rev（模拟 super-step 落盘）
        for rev in (1, 2):
            store.save(
                run_id,
                rev,
                CheckpointNode.FINAL,
                f"checkpoint/{run_id}/{rev}.json",
                f"h{rev}",
            )
        # 新 max rev 行仍应带 worker-a 的租约 → B 抢不到（抛业务冲突）
        with pytest.raises(CoreError) as ei:
            provider.acquire(run_id, "worker-b", ttl_s=30)
        assert ei.value.code == "HUMAN_REQUIRED"

    def test_expired_lease_takeover(self) -> None:
        """租约过期后另一 worker 可接管（A3 崩溃恢复的 SQL 判定）。"""
        provider = PgLeaseProvider(PG_DSN)
        run_id = f"run-{uuid4()}"
        provider.acquire(run_id, "worker-a", ttl_s=1)
        # 直接把 lease_until 改为过去，模拟 worker 崩溃后租约过期
        with psycopg.connect(PG_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE checkpoint SET lease_until = now() - interval '1 minute' "
                    "WHERE run_id = %s",
                    (run_id,),
                )
            conn.commit()
        assert run_id in provider.scan_expired(datetime.now(UTC))
        takeover = provider.acquire(run_id, "worker-b", ttl_s=30)
        assert takeover is not None
        assert takeover.owner == "worker-b"


class TestPgCheckpoint:
    """A10：真实 PG 落盘 + hash 链。"""

    def test_save_and_load_rev_chain(self) -> None:
        """落 3 个 rev：latest_rev=3、hashes 有序、run_id 隔离。"""
        run_id = f"run-{uuid4()}"
        store = PgCheckpointStore(PG_DSN, object_get=lambda ref: b"{}")
        for rev in range(1, 4):
            store.save(
                run_id,
                rev,
                CheckpointNode.FINAL,
                f"checkpoint/{run_id}/{rev}.json",
                f"h{rev}",
            )
        assert store.latest_rev(run_id) == 3
        assert store.hashes(run_id) == ["h1", "h2", "h3"]

    def test_duplicate_rev_rejected_by_pk(self) -> None:
        """重复 (run_id, rev) 触发主键冲突（I1，真实 PG 约束）。"""
        run_id = f"run-{uuid4()}"
        store = PgCheckpointStore(PG_DSN, object_get=lambda ref: b"{}")
        store.save(run_id, 1, CheckpointNode.FINAL, "ref1", "h1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            store.save(run_id, 1, CheckpointNode.FINAL, "ref2", "h2")


class TestRedisInfra:
    """A11/A5：真实 Redis Streams 扇出与 SETNX 幂等。"""

    def test_stream_emit_window_resume(self) -> None:
        """XADD 5 条 + XTRIM 窗口 3 → window 只返回最近 3 条（A11）。"""
        from packages.agent_core.types import AgentEvent
        from packages.contracts.enums import AgentEventKind

        def decode(raw: str) -> AgentEvent | None:
            d = json.loads(raw)
            return AgentEvent(
                seq=d["seq"],
                session_id="s1",
                run_id="r1",
                ts="",
                kind=AgentEventKind(d["kind"]),
                phase="RUNNING",
                payload=d["payload"],
                trace_id="t1",
            )

        bus = RedisEventBus(REDIS_URL, window_n=3, event_decode=decode)
        for seq in range(1, 6):
            bus.emit(
                AgentEvent(
                    seq=seq,
                    session_id="s1",
                    run_id="r1",
                    ts="",
                    kind=AgentEventKind.RUN,
                    phase="RUNNING",
                    payload={"n": seq},
                    trace_id="t1",
                )
            )
        events = bus.window("s1", n=10)
        assert [e.seq for e in events] == [3, 4, 5]

    def test_idempotency_setnx(self) -> None:
        """SETNX 幂等：首次占位 None，commit 后命中复用（A5）。"""
        store = RedisIdempotencyStore(REDIS_URL)
        key = f"idem-{uuid4()}"
        assert store.get_or_reserve(key, "run-1") is None
        store.commit(key, "result-ref-1")
        assert store.get_or_reserve(key, "run-2") == "result-ref-1"


class TestPgStores:
    """真实 PG 存储：数据落库、跨实例可读、租户隔离（生产接线验证）。"""

    def test_user_persists_and_rejects_duplicate(self) -> None:
        """用户落 PG 后新实例可读；重复邮箱由 UNIQUE 约束拒绝（A2）。"""
        store = PgUserStore(PG_DSN)
        email = f"pg-{uuid4()}@example.com"
        row = UserRow(id=str(uuid4()), email=email, password_hash="argon2$hash")
        store.insert(row)
        # 换一个 store 实例读，证明数据在 PG 不在内存
        assert PgUserStore(PG_DSN).find_by_email(email) is not None
        with pytest.raises(DuplicateEmailError):
            store.insert(UserRow(id=str(uuid4()), email=email, password_hash="x"))

    def test_session_state_machine_persists(self) -> None:
        """会话状态迁移落 PG：建→提交→打断→恢复，跨实例可读（跨天恢复基础）。"""
        store = PgSessionStore(PG_DSN)
        svc = SessionService(store, audit=AuditLedger())
        sid = str(uuid4())
        row = SessionRow(
            id=sid, user_id="pg-user", state=SessionState.DRAFT, grid="4x4"
        )
        store.insert(row)
        svc.submit_message(sid, "pg-user", "desc", [])
        assert PgSessionStore(PG_DSN).get(sid).state is SessionState.RUNNING  # type: ignore[union-attr]
        svc.interrupt(sid, "pg-user")
        assert PgSessionStore(PG_DSN).get(sid).state is SessionState.PAUSED  # type: ignore[union-attr]
        svc.resume(sid, "pg-user")
        assert PgSessionStore(PG_DSN).get(sid).state is SessionState.RUNNING  # type: ignore[union-attr]

    def test_optimistic_lock_rejects_stale_transition(self) -> None:
        """S2 回归：两个服务实例持同一会话旧态并发迁移，后者因乐观锁失败。"""
        from services.control_plane.sessions.service import ConflictError

        store = PgSessionStore(PG_DSN)
        svc_a = SessionService(store, audit=AuditLedger())
        svc_b = SessionService(store, audit=AuditLedger())  # 模拟另一个 worker 的独立实例
        sid = str(uuid4())
        store.insert(SessionRow(id=sid, user_id="u", state=SessionState.DRAFT))
        # A 先提交 DRAFT→RUNNING
        svc_a.submit_message(sid, "u", "desc", [])
        # B 拿到的仍是过期的 DRAFT 快照，尝试迁移应被乐观锁拒绝
        stale = store.get(sid)
        assert stale is not None
        stale.state = SessionState.DRAFT  # 模拟 B 读到的旧态
        with pytest.raises(ConflictError):
            svc_b._commit(stale, SessionState.DRAFT)

    def test_session_tenant_isolation(self) -> None:
        """跨用户读会话由 TenantGuard 拒绝（A1，PG 存储路径）。"""
        from services.control_plane.api.guard import ForbiddenError

        store = PgSessionStore(PG_DSN)
        svc = SessionService(store, audit=AuditLedger())
        sid = str(uuid4())
        store.insert(SessionRow(id=sid, user_id="owner", state=SessionState.DRAFT))
        with pytest.raises(ForbiddenError):
            svc.snapshot(sid, "intruder")

    def test_auth_with_pg_store_end_to_end(self) -> None:
        """AuthService 注入 PgUserStore：注册→登录→JWT，全链路 PG（生产同构）。"""
        store = PgUserStore(PG_DSN)
        auth = AuthService(
            store, jwt_secret="test-secret-not-production-32bytes-minimum"
        )
        result = auth.register(f"auth-{uuid4()}@example.com", "pass-phrase")
        # 用新 AuthService + 新 store 实例登录，证明凭据与 token 跨实例有效
        auth2 = AuthService(
            PgUserStore(PG_DSN), jwt_secret="test-secret-not-production-32bytes-minimum"
        )
        logged = auth2.login(result.user.email, "pass-phrase")
        assert auth2.decode_access(logged.access_token).user_id == result.user.id
