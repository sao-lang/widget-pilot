"""ADR-009 会话事实摘要的 PostgreSQL 真实存储（fail-closed）。

(session_id, turn_index) 唯一，只追加不删行；advisory xact lock 串行化写入，
ON CONFLICT DO NOTHING 保证重入幂等。PG 失败抛异常——不降级内存、不静默丢事实。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from packages.agent_core.types import JsonValue
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models_fact_summary import FactSummary as FactSummaryRow

_FACT_ADVISORY_KEY: int = 2026112201


def _append(session: Session, session_id: str, turn_index: int,
            payload: JsonValue, rendered: str) -> None:
    """在已加锁事务内追加一行（同键冲突跳过，幂等）。"""
    session.execute(pg_insert(FactSummaryRow).values(
        session_id=session_id, turn_index=turn_index,
        payload=payload, rendered=rendered,
    ).on_conflict_do_nothing(index_elements=["session_id", "turn_index"]))


class PgFactSummaryStore:
    """fact_summary 表的真实 PG 实现（生产注入 dispatcher）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短 Session）。"""
        self._factory = make_session_factory(dsn)

    def latest(self, session_id: str) -> tuple[int, str]:
        """读取最新事实；无记录返回 (0, "")（读取侧容错，装配省略该段）。"""
        with session_scope(self._factory) as session:
            row = session.execute(
                select(FactSummaryRow.turn_index, FactSummaryRow.rendered)
                .where(FactSummaryRow.session_id == session_id)
                .order_by(FactSummaryRow.turn_index.desc())
                .limit(1)
            ).first()
        if row is None:
            return 0, ""
        return int(row.turn_index), str(row.rendered)

    def append(
        self,
        session_id: str,
        turn_index: int,
        payload: JsonValue,
        rendered: str,
    ) -> None:
        """加锁追加事实（PG 失败抛异常，fail-closed）。"""
        with session_scope(self._factory) as session:
            session.execute(select(func.pg_advisory_xact_lock(_FACT_ADVISORY_KEY)))
            _append(session, session_id, turn_index, payload, rendered)
