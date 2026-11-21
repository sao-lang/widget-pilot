"""M2（v1.8）：三表 PG 留证写入失败被隔离，不击穿校验链。

用 monkeypatch 让 psycopg.connect 抛错（不连真库，快速），验证三个 Store 的
append 吞掉 psycopg.Error 并返回——PG 抖动只丢证据，不打挂正常业务。
"""

from __future__ import annotations

import psycopg
import pytest

from services.guardrails import pg_stores
from services.guardrails.telemetry import CapabilityAuditLogRow, IntentRouteLogRow
from services.guardrails.types import HitDecision, RouteLayer


@pytest.fixture
def broken_pg(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 psycopg.connect 抛连接错误（模拟 PG 抖动）。"""

    def raise_conn(*args: object, **kwargs: object) -> object:
        """抛出连接失败。

        反射边界豁免：psycopg.connect 的真实返回是参数化 Connection，
        这里只用于 monkeypatch 抛错，返回值永不被读取，故保留宽泛签名。
        """
        raise psycopg.OperationalError("simulated PG outage")

    monkeypatch.setattr(pg_stores.psycopg, "connect", raise_conn)


class TestAppendFailureIsolated:
    """M2：append 失败不冒泡。"""

    def test_intent_route_append_isolated(self, broken_pg: None) -> None:
        """IntentRouteLogStore.append 吞 psycopg.Error。"""
        store = pg_stores.PgIntentRouteLogStore("postgresql://x")
        store.append(
            IntentRouteLogRow(
                "s", "r", RouteLayer.L1, "intent", 0.9, 1, "v1", "t", None
            )
        )  # 不抛即通过

    def test_capability_audit_append_isolated(self, broken_pg: None) -> None:
        """能力审计 Store.append 吞 psycopg.Error。"""
        store = pg_stores.PgCapabilityAuditLogStore("postgresql://x")
        store.append(
            CapabilityAuditLogRow(
                "s", "r", "recipe", 1, 1, 10, HitDecision.REJECT, "limit", "t"
            )
        )
