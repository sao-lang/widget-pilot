"""成本账的 PostgreSQL 只追加存储（M07 §3.4 D / B7）。

同 ``guard_hit_pg`` 纪律：**PG 连接/写入失败抛异常，绝不降级为非持久化实现**——
成本账是「计量执行」的法定留证（M07 §2.6），丢账即无法出账与复现对账。
幂等：``run_id`` 唯一，``ON CONFLICT DO NOTHING`` 保证重放/重试不重复记账。
SQL 由 SQLAlchemy ORM/方言表达，不依赖具体 DBAPI。
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import CostLedger
from services.orchestrator.cost.ports import CostEntry


class PgCostLedger:
    """成本账 PG 存储：run_id 唯一幂等追加，PG 失败抛异常（fail-closed）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次写入开短 Session，跨实例天然幂等）。

        Args:
            dsn: PostgreSQL 连接串（兼容任意驱动前缀）。
        """
        self._factory = make_session_factory(dsn)

    def append(self, entry: CostEntry) -> None:
        """追加一条成本账目；同 run_id 已存在则静默跳过（幂等）。

        Raises:
            SQLAlchemyError: 连接或写入失败——成本证据不可丢，不吞异常不降级。
        """
        stmt = (
            pg_insert(CostLedger)
            .values(
                run_id=entry.run_id,
                session_id=entry.session_id,
                user_id=entry.user_id,
                model_tier=entry.model_tier,
                tokens=entry.tokens,
                iterations=entry.iterations,
                tool_calls=entry.tool_calls,
                sandbox_minutes=entry.sandbox_minutes,
                repro_fingerprint=entry.repro_fingerprint,
                created_at=entry.created_at,
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        with session_scope(self._factory) as session:
            session.execute(stmt)
