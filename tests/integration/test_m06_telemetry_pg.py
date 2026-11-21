"""M06 只追加日志 PG 持久化集成测试（M04 §9.0 接线义务 3）。

两张 telemetry 表（intent_route / capability_audit）落 PG 后，新 Store 实例能从库里读回并复算统计——
证明是落库而非内存。guard_hit 的 PG 留证单独在 test_m06_guard_hit_pg.py 覆盖（hash 链语义独立）。

前置：docker compose --profile core up -d（PG:5432）；
本机无容器时整文件 skip（连通性守卫），不冒充通过。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Final, LiteralString, cast

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from services.guardrails.pg_stores import (
    PgCapabilityAuditLogStore,
    PgIntentRouteLogStore,
)
from services.guardrails.telemetry import CapabilityAuditLogRow, IntentRouteLogRow
from services.guardrails.types import HitDecision, RouteLayer

PG_DSN: Final[str] = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"

_TABLE_INTENT_ROUTE: Final[str] = "intent_route_log"
_TABLE_CAPABILITY_AUDIT: Final[str] = "capability_audit_log"

# 建表 DDL 与迁移 0005_m06_telemetry 对齐；IF NOT EXISTS 保证幂等兜底（不跑 alembic）。
_SCHEMA: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {_TABLE_INTENT_ROUTE} (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        route_layer TEXT NOT NULL,
        intent TEXT NOT NULL,
        similarity DOUBLE PRECISION,
        latency_ms INTEGER NOT NULL,
        samples_version TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '',
        fallback_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"CREATE INDEX IF NOT EXISTS ix_intent_route_log_session_id ON {_TABLE_INTENT_ROUTE} (session_id)",
    f"CREATE INDEX IF NOT EXISTS ix_intent_route_log_created_at ON {_TABLE_INTENT_ROUTE} (created_at)",
    f"""
    CREATE TABLE IF NOT EXISTS {_TABLE_CAPABILITY_AUDIT} (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        capability_id TEXT NOT NULL,
        level INTEGER NOT NULL,
        turn_count INTEGER NOT NULL,
        tokens INTEGER NOT NULL,
        decision TEXT,
        reason TEXT,
        trace_id TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"CREATE INDEX IF NOT EXISTS ix_capability_audit_log_session_id ON {_TABLE_CAPABILITY_AUDIT} (session_id)",
    f"CREATE INDEX IF NOT EXISTS ix_capability_audit_log_created_at ON {_TABLE_CAPABILITY_AUDIT} (created_at)",
)


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


@pytest.fixture(scope="module", autouse=True)
def prepare_tables() -> None:
    """模块开始前建三张表 + 索引（幂等，不破坏现有库）。"""
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            for ddl in _SCHEMA:
                cur.execute(ddl)
        conn.commit()


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """每例前清空三张表（集成测试隔离）。"""
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"TRUNCATE TABLE {_TABLE_INTENT_ROUTE}, {_TABLE_CAPABILITY_AUDIT}"
            )
        conn.commit()


def _db_count(table: LiteralString) -> int:
    """直接查库行数，作为「真的落库」的独立证据（不经过 Store）。"""
    with cast(
        psycopg.Connection[DictRow],
        # psycopg 反射边界豁免：dict_row 的精确类型随 psycopg 版本变化，
        # 此处仅作为 row_factory 透传，保留 cast(Any, ...) 不新增 ignore。
        psycopg.connect(PG_DSN, row_factory=cast(Any, dict_row)),
    ) as conn:
        row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
    assert row is not None
    return int(row["n"])


class TestPgIntentRouteLogStore:
    """IntentRouteLog 落 PG：stats 复算 + 跨实例可读回。"""

    def test_stats_and_cross_instance_readback(self) -> None:
        """追加 L1/L1/L2/L3/L4 五条：各级占比与升级率复算正确，新实例读回一致。"""
        store = PgIntentRouteLogStore(PG_DSN)
        samples: tuple[IntentRouteLogRow, ...] = (
            IntentRouteLogRow("s1", "r1", RouteLayer.L1, "widget", 0.9, 10, "v1", "t1"),
            IntentRouteLogRow("s1", "r2", RouteLayer.L1, "widget", 0.8, 12, "v1", "t2"),
            IntentRouteLogRow("s1", "r3", RouteLayer.L2, "widget", 0.7, 15, "v1", "t3"),
            IntentRouteLogRow("s1", "r4", RouteLayer.L3, "widget", None, 30, "v1", "t4", "L3 不可解析"),
            IntentRouteLogRow("s1", "r5", RouteLayer.L4, "widget", None, 50, "v1", "t5", None),
        )
        for row in samples:
            store.append(row)

        stats = store.stats()
        assert stats["L1"] == pytest.approx(0.4)
        assert stats["L2"] == pytest.approx(0.2)
        assert stats["L3"] == pytest.approx(0.2)
        assert stats["L4"] == pytest.approx(0.2)
        assert stats["escalation_rate"] == pytest.approx(0.4)

        # 跨实例：新建 Store 从 PG 读回，证明不是内存态
        rebuilt = PgIntentRouteLogStore(PG_DSN)
        assert rebuilt.stats() == stats
        assert _db_count(_TABLE_INTENT_ROUTE) == len(samples)

    def test_empty_stats_matches_memory_contract(self) -> None:
        """空表 statistics 全零，与内存实现契约一致。"""
        stats = PgIntentRouteLogStore(PG_DSN).stats()
        assert stats == {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0, "escalation_rate": 0.0}


class TestPgCapabilityAuditLogStore:
    """能力审计日志落 PG：decisions 聚合 + None 决策 + 跨实例。"""

    def test_decisions_and_cross_instance_readback(self) -> None:
        """三种决策 + 一次放行(None)：Counter 计数含 None，新实例读回一致。"""
        store = PgCapabilityAuditLogStore(PG_DSN)
        samples: tuple[CapabilityAuditLogRow, ...] = (
            CapabilityAuditLogRow("s1", "r1", "r_a", 1, 1, 10, HitDecision.REJECT, "越界", "t1"),
            CapabilityAuditLogRow("s1", "r2", "r_a", 1, 1, 10, HitDecision.MASK, "脱敏", "t2"),
            CapabilityAuditLogRow("s1", "r3", "r_b", 1, 2, 20, HitDecision.AWAIT, "待确认", "t3"),
            CapabilityAuditLogRow("s1", "r4", "r_c", 1, 3, 30, HitDecision.REJECT, "白名单", "t4"),
            CapabilityAuditLogRow("s1", "r5", "r_c", 1, 3, 30, None, None, "t5"),
        )
        for row in samples:
            store.append(row)

        expected = Counter(
            {
                HitDecision.REJECT: 2,
                HitDecision.MASK: 1,
                HitDecision.AWAIT: 1,
                None: 1,
            }
        )
        assert store.decisions() == expected

        rebuilt = PgCapabilityAuditLogStore(PG_DSN)
        assert rebuilt.decisions() == expected
        assert _db_count(_TABLE_CAPABILITY_AUDIT) == len(samples)
