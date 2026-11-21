"""成本账的 PostgreSQL 只追加存储（M07 §3.4 D / B7）。

同 ``guard_hit_pg`` 纪律：**PG 连接/写入失败抛异常，绝不降级为非持久化实现**——
成本账是「计量执行」的法定留证（M07 §2.6），丢账即无法出账与复现对账。
幂等：``run_id`` 唯一，``ON CONFLICT DO NOTHING`` 保证重放/重试不重复记账。
"""
from __future__ import annotations

from typing import Final

import psycopg

from services.orchestrator.cost.ports import CostEntry

_TABLE: Final[str] = "cost_ledger"
_ASYNCPG_PREFIX: Final[str] = "postgresql+asyncpg://"


def _sync_dsn(url: str) -> str:
    """asyncpg DSN（compose 生产格式）→ psycopg 同步 DSN（B1）。

    Args:
        url: 原始 DSN，可能是 SQLAlchemy asyncpg 格式。

    Returns:
        psycopg 可直接连接的同步 DSN。
    """
    return (
        url.replace(_ASYNCPG_PREFIX, "postgresql://", 1)
        if url.startswith(_ASYNCPG_PREFIX)
        else url
    )


class PgCostLedger:
    """成本账 PG 存储：run_id 唯一幂等追加，PG 失败抛异常（fail-closed）。

    Attributes:
        _dsn: 归一化后的 psycopg 同步 DSN（asyncpg 前缀已去除）。
    """

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次写入开短连接，跨实例天然幂等）。

        Args:
            dsn: PostgreSQL 连接串（兼容 asyncpg 前缀）。
        """
        self._dsn = _sync_dsn(dsn)

    def append(self, entry: CostEntry) -> None:
        """追加一条成本账目；同 run_id 已存在则静默跳过（幂等）。

        Args:
            entry: 一条成本账目（十字段齐全）。

        Raises:
            psycopg.Error: 连接或写入失败——成本证据不可丢，不吞异常不降级。
        """
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                f"INSERT INTO {_TABLE} "
                "(run_id, session_id, user_id, model_tier, tokens, iterations, "
                "tool_calls, sandbox_minutes, repro_fingerprint, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (run_id) DO NOTHING",
                (
                    entry.run_id,
                    entry.session_id,
                    entry.user_id,
                    entry.model_tier,
                    entry.tokens,
                    entry.iterations,
                    entry.tool_calls,
                    entry.sandbox_minutes,
                    entry.repro_fingerprint,
                    entry.created_at,
                ),
            )
