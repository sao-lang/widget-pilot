"""M07 成本账真 PG 集成测试（B7）。

验证 append 真落库读回、同 run_id 幂等不重复、repro_fingerprint 落库一致——
全部走真实 PG，不做内存替身。PG 不可达则 skip（不冒充通过）。

前置：docker compose --profile core up -d + alembic upgrade head（含 0011）。
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from services.orchestrator.cost.pg_ledger import PgCostLedger
from services.orchestrator.cost.ports import CostEntry
from tests.support.pg import (
    pg_available,
    pg_connection,
    pg_execute,
    pg_fetchone,
    pg_scalar,
)

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
_TABLE = "cost_ledger"
_FIXED_TIME = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


def _infra_available() -> bool:
    """探测 PG 可达性；不可达则 skip。"""
    return pg_available(PG_DSN)


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_cost_ledger() -> None:
    """每例前清空 cost_ledger 表（集成测试隔离）。"""
    with pg_connection(PG_DSN) as conn:
        pg_execute(conn, f"TRUNCATE TABLE {_TABLE} RESTART IDENTITY")


def _entry() -> CostEntry:
    """构造一条固定的成本账目（十字段齐全，便于逐字段读回断言）。"""
    return CostEntry(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        model_tier="standard",
        tokens=420,
        iterations=3,
        tool_calls=2,
        sandbox_minutes=1.5,
        repro_fingerprint="a" * 64,
        created_at=_FIXED_TIME,
    )


def _read_row(run_id: str) -> Mapping[str, Any] | None:
    """从 PG 读回指定 run 的账目行（dict 行视图）。"""
    with pg_connection(PG_DSN) as conn:
        row = pg_fetchone(
            conn,
            f"SELECT run_id, session_id, user_id, model_tier, tokens, iterations, "
            f"tool_calls, sandbox_minutes, repro_fingerprint, created_at "
            f"FROM {_TABLE} WHERE run_id = %s",
            (run_id,),
        )
    return None if row is None else cast(Mapping[str, Any], row._mapping)


class TestPgCostLedger:
    """cost_ledger 真 PG 落库、幂等与指纹一致。"""

    def test_append_persists_and_reads_back(self) -> None:
        """append 真落库；新建实例之外用独立连接可读回全部十字段。"""
        entry = _entry()
        PgCostLedger(PG_DSN).append(entry)

        row = _read_row(entry.run_id)
        assert row is not None
        assert row["session_id"] == entry.session_id
        assert row["user_id"] == entry.user_id
        assert row["model_tier"] == entry.model_tier
        assert row["tokens"] == entry.tokens
        assert row["iterations"] == entry.iterations
        assert row["tool_calls"] == entry.tool_calls
        assert row["sandbox_minutes"] == entry.sandbox_minutes
        assert row["created_at"] == entry.created_at

    def test_same_run_id_is_idempotent(self) -> None:
        """同 run_id 重复 append 不重复记账（ON CONFLICT DO NOTHING）。"""
        ledger = PgCostLedger(PG_DSN)
        ledger.append(_entry())
        ledger.append(_entry())

        with pg_connection(PG_DSN) as conn:
            count = pg_scalar(
                conn, f"SELECT count(*) FROM {_TABLE} WHERE run_id = %s", ("run-1",)
            )
        assert count == 1

    def test_repro_fingerprint_persisted(self) -> None:
        """repro_fingerprint（含 preference_version 算出的值）逐字落库。"""
        entry = _entry()
        PgCostLedger(PG_DSN).append(entry)
        row = _read_row(entry.run_id)
        assert row is not None
        assert row["repro_fingerprint"] == entry.repro_fingerprint
        assert len(str(row["repro_fingerprint"])) == 64
