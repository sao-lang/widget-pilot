"""M04 审计账本 PG 持久化集成测试（A18：只追加 hash 链可完整重放）。

补充单测内存账本无法覆盖的部分：审计落 PG 表后，账本实例重建仍能从库里
重算 hash 链、按会话回放；SessionService 的状态迁移真实写库而非内存。

前置：docker compose --profile core up -d（PG:5432）；
本机无容器时整文件 skip（连通性守卫），不冒充通过。
"""

from __future__ import annotations

import concurrent.futures

import psycopg
import pytest

from services.control_plane.accounts.constants import LEASE_TTL_S  # noqa: F401 - 固化常量守卫
from services.control_plane.audit.ledger import AuditActions
from services.control_plane.audit.pg_ledger import PgAuditLedger
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.control_plane.sessions.service import SessionService

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"


def _infra_available() -> bool:
    """探测 PG 是否可达；不可达则跳过（不冒充通过）。"""
    try:
        with psycopg.connect(PG_DSN, connect_timeout=2):
            pass
    except Exception:  # noqa: BLE001 - 连通性探测必须吞掉任意连接错误以决定 skip
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_audit() -> None:
    """每例前清空审计与会话表（集成测试隔离）。"""
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE audit_event, sessions CASCADE")
        conn.commit()


class TestPgAuditLedger:
    """A18：审计 hash 链落 PG 且可跨实例重放。"""

    def test_append_builds_chain_in_pg(self) -> None:
        """连续追加：prev_hash 逐条链接，chain_valid 为真。"""
        ledger = PgAuditLedger(PG_DSN)
        r1 = ledger.append(
            "u1", "s1", AuditActions.STATE_TRANSITION, "OK", {"from": "DRAFT"}, "t1"
        )
        r2 = ledger.append("u1", "s1", AuditActions.INTERRUPT, "OK", {"rev": 1}, "t2")
        assert r1.prev_hash == ""
        assert r2.prev_hash == r1.chain_hash
        assert ledger.chain_valid() is True

    def test_chain_survives_instance_rebuild(self) -> None:
        """新实例从 PG 重建链并重算——证明持久化而非内存（B2 核心断言）。"""
        PgAuditLedger(PG_DSN).append(
            "u1", "s1", AuditActions.RESUME, "OK", {"rev": 2}, "t1"
        )
        rebuilt = PgAuditLedger(PG_DSN)
        rows = rebuilt.by_session("s1")
        assert len(rows) == 1
        assert rebuilt.chain_valid() is True
        assert rows[0].action == AuditActions.RESUME

    def test_tamper_in_pg_detected(self) -> None:
        """直接改库内 decision 字段后 chain_valid 返回 False（S6 防篡改）。"""
        ledger = PgAuditLedger(PG_DSN)
        ledger.append("u1", "s1", AuditActions.RESUME, "OK", {}, "t1")
        with psycopg.connect(PG_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE audit_event SET decision = 'TAMPERED'")
            conn.commit()
        assert ledger.chain_valid() is False

    def test_by_session_isolates_tenants(self) -> None:
        """按会话过滤：跨会话事件互不串（租户重放入口）。"""
        ledger = PgAuditLedger(PG_DSN)
        ledger.append("u1", "s1", AuditActions.CANCEL, "OK", {}, "t1")
        ledger.append("u2", "s2", AuditActions.CANCEL, "OK", {}, "t2")
        assert len(ledger.by_session("s1")) == 1
        assert len(ledger.by_session("s2")) == 1

    def test_session_service_writes_audit_to_pg(self) -> None:
        """R10 生产接线：SessionService 迁移真实写 PG 审计，新实例可回放。"""
        ledger = PgAuditLedger(PG_DSN)
        svc = SessionService(PgSessionStore(PG_DSN), audit=ledger)
        sid = svc.create("u1", "t", "4x4").id
        svc.submit_message(sid, "u1", "desc", [])
        svc.interrupt(sid, "u1")
        rows = PgAuditLedger(PG_DSN).by_session(sid)
        assert len(rows) == 2  # 提交 + 打断
        assert rows[0].action == AuditActions.STATE_TRANSITION
        assert rows[1].action == AuditActions.INTERRUPT
        assert PgAuditLedger(PG_DSN).chain_valid() is True

    def test_concurrent_append_keeps_chain_valid(self) -> None:
        """review B4：20 并发 append 后 hash 链不断（advisory lock 串行化）。"""
        ledger = PgAuditLedger(PG_DSN)

        def append(i: int) -> None:
            ledger.append("u1", "s1", AuditActions.RESUME, "OK", {"i": i}, f"t{i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(append, range(20)))
        assert len(ledger.all()) == 20
        assert PgAuditLedger(PG_DSN).chain_valid() is True
