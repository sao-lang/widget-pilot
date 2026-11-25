"""ADR-019 D3：分流决策 occurrence 存储（每次请求一条，含非终态）。

与成本账分工：cost_ledger 只记终态成本，本表记全分支漏斗（clarification /
开关 off / 通道异常这些不落账的分支也留痕）。PG 写入失败抛异常，不降级。
"""
from __future__ import annotations

from sqlalchemy import insert, select

from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import GenerationOccurrence


class PgGenerationOccurrenceStore:
    """generation_occurrence 的 PG 追加存储（fail-closed）。"""

    def __init__(self, dsn: str) -> None:
        self._factory = make_session_factory(dsn)

    def append(
        self, session_id: str, run_id: str, generation_path: str, branch: str
    ) -> None:
        """追加一条分流决策留证。"""
        with session_scope(self._factory) as session:
            session.execute(
                insert(GenerationOccurrence).values(
                    session_id=session_id, run_id=run_id,
                    generation_path=generation_path, branch=branch,
                )
            )

    def stats(self, session_id: str) -> dict[str, int]:
        """按 generation_path 分组计数（三口径统计的基础）。"""
        with session_scope(self._factory) as session:
            rows = session.execute(
                select(
                    GenerationOccurrence.generation_path,
                    GenerationOccurrence.id,
                ).where(GenerationOccurrence.session_id == session_id)
            ).all()
        counts: dict[str, int] = {}
        for path, _id in rows:
            counts[path] = counts.get(path, 0) + 1
        return counts
