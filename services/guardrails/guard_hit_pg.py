"""guard_hit 的 PostgreSQL 只追加存储（runbook §2 定稿，2026-11-21 补齐）。

与内存版接口一致（log_hit / verify_chain / false_positive_rate / rows），
但落 guard_hit 表 + prev_hash hash 链；写入在单事务内对全表加 advisory xact lock，
防并发断链。所有 SQL 走 SQLAlchemy ORM，不依赖具体 DBAPI。

**不可用语义（M06 §2.3）**：PG 连接/写入失败时**抛异常**，由装配层保守拒绝放行——
绝不降级为内存替身（无证据放行）。这与三表日志（append 失败只丢证据）相反，
因为 guard_hit 是「命中即不放行」路径的法定留证。
"""

from __future__ import annotations

import hashlib
from typing import Final

from sqlalchemy import func, select

from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import GuardHit
from services.guardrails.guard_hit import GuardHitRow
from services.guardrails.types import GuardHitType, InputGuardHit

_GENESIS: Final[str] = "0" * 64
# guard_hit 追加的全局事务锁键（任意固定 bigint；只用于串行化本表 append）。
_ADVISORY_KEY: Final[int] = 2026112101


def _chain_hash(row: GuardHitRow) -> str:
    """复算行 hash（与内存版 _chain_hash 逐字段一致，防两实现漂移）。"""
    raw = "|".join(
        [
            str(row.id),
            row.session_id,
            row.run_id,
            row.prev_hash,
            row.hit_type.value
            if hasattr(row.hit_type, "value")
            else str(row.hit_type),
            row.rule_id,
            row.judgement,
            row.excerpt,
            row.decision,
            row.trace_id,
            "1" if row.should_pass else "0" if row.should_pass is False else "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PgGuardHitStore:
    """guard_hit PG 存储：hash 链只追加，PG 失败抛异常（保守拒绝）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短 Session，跨实例天然读回同一链）。"""
        self._factory = make_session_factory(dsn)

    def log_hit(
        self,
        session_id: str,
        run_id: str,
        hit: InputGuardHit,
        trace_id: str,
        should_pass: bool | None = None,
    ) -> int:
        """追加命中留证，返回自增 id；PG 失败抛 SQLAlchemyError（不放行）。

        事务内先取 advisory lock 串行化 append → SELECT 末行 → 算 hash → INSERT，
        保证并发命中不产生重复 id/断链。
        """
        decision = (
            hit.decision.value if hasattr(hit.decision, "value") else str(hit.decision)
        )
        with session_scope(self._factory) as session:
            # 事务级 advisory lock 串行化 append——READ COMMITTED 下仅锁末行不足
            # （并发事务看不到对方新插入的行，仍会算重 id 撞 PK 丢留证）。
            session.execute(select(func.pg_advisory_xact_lock(_ADVISORY_KEY)))
            last = session.execute(
                select(GuardHit.id, GuardHit.row_hash)
                .order_by(GuardHit.id.desc())
                .limit(1)
            ).first()
            row_id = int(last.id) + 1 if last else 1
            prev_hash = str(last.row_hash) if last else _GENESIS
            row = GuardHitRow(
                id=row_id,
                session_id=session_id,
                run_id=run_id,
                hit_type=hit.hit_type,
                rule_id=hit.rule_id,
                judgement=hit.judgement,
                excerpt=hit.excerpt,
                decision=decision,
                trace_id=trace_id,
                should_pass=should_pass,
                prev_hash=prev_hash,
                row_hash="",
            )
            row.row_hash = _chain_hash(row)
            session.add(
                GuardHit(
                    id=row.id,
                    session_id=row.session_id,
                    run_id=row.run_id,
                    hit_type=row.hit_type.value,
                    rule_id=row.rule_id,
                    judgement=row.judgement,
                    excerpt=row.excerpt,
                    stage="INPUT",
                    decision=row.decision,
                    trace_id=row.trace_id,
                    prev_hash=row.prev_hash,
                    row_hash=row.row_hash,
                    should_pass=row.should_pass,
                )
            )
        return row_id

    @property
    def rows(self) -> list[GuardHitRow]:
        """从 PG 读回全部命中行（id 升序；与内存版 rows 接口一致）。"""
        with session_scope(self._factory) as session:
            raw = session.execute(
                select(
                    GuardHit.id,
                    GuardHit.session_id,
                    GuardHit.run_id,
                    GuardHit.hit_type,
                    GuardHit.rule_id,
                    GuardHit.judgement,
                    GuardHit.excerpt,
                    GuardHit.decision,
                    GuardHit.trace_id,
                    GuardHit.prev_hash,
                    GuardHit.row_hash,
                    GuardHit.should_pass,
                ).order_by(GuardHit.id)
            ).all()
        return [
            GuardHitRow(
                id=int(r.id),
                session_id=str(r.session_id),
                run_id=str(r.run_id),
                hit_type=GuardHitType(str(r.hit_type)),
                rule_id=str(r.rule_id),
                judgement=str(r.judgement),
                excerpt=str(r.excerpt),
                decision=str(r.decision),
                trace_id=str(r.trace_id),
                should_pass=r.should_pass,
                prev_hash=str(r.prev_hash),
                row_hash=str(r.row_hash),
            )
            for r in raw
        ]

    def verify_chain(self) -> bool:
        """从 PG 读回全链校验：prev_hash 衔接 + row_hash 可复算。"""
        rows = self.rows
        for index, row in enumerate(rows):
            expected_prev = rows[index - 1].row_hash if index > 0 else _GENESIS
            if row.prev_hash != expected_prev or row.row_hash != _chain_hash(row):
                return False
        return True

    def false_positive_rate(self, labeled_should_pass: int) -> float:
        """从 PG 聚合 FPR = should_pass=True 数 / 标注应放行总数。"""
        if labeled_should_pass <= 0:
            return 0.0
        with session_scope(self._factory) as session:
            count = session.scalar(
                select(func.count())
                .select_from(GuardHit)
                .where(GuardHit.should_pass.is_(True))
            )
        return int(count or 0) / labeled_should_pass
