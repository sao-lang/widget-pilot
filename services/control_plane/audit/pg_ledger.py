"""M04 审计账本的 PG 持久化实现（§7 / §8.2 A18）。

与内存 :class:`AuditLedger` 同接口（append/chain_valid/by_session/all），
hash 链算法复用 ledger 的规范化函数——生产重启后新实例仍能重算整条链，
满足 A18「任一会话可完整重放审计」。append 在单 Session 事务内取链尾、
算 hash、写行，保证并发下 seq 单调与 prev_hash 链不裂。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Final, cast

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.control_plane.audit.ledger import (
    AuditEventRow,
    AuditPayload,
    _canonical_body,
)
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import AuditEvent

_ADMIN_ACTOR: Final[str] = "system"
"""无可用 user_id 时的 actor 占位（DDL 要求 actor NOT NULL，内存账本无此字段）。"""
_CHAIN_LOCK_KEY: Final[int] = 0x4D04_1A18
"""审计链 advisory lock key（M04 A18 魔数），串行化「读尾→算 hash→写行」全段。

空表时 FOR UPDATE 无行可锁，故用会话级 advisory lock 覆盖并发首写；
事务结束自动释放（session_scope 提交/回滚）。
"""


class PgAuditLedger:
    """PG 只追加审计账本（A18：hash 链跨实例可重算）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN，构造该实例绑定的 sessionmaker。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._factory = make_session_factory(dsn)
        """SQLAlchemy sessionmaker（每次 append / 查询开短 Session）。"""

    @staticmethod
    def _normalize_payload(payload: AuditPayload) -> AuditPayload:
        """把载荷收敛为可 JSON 往返的标量形态（写入与重算共用同一规范化点）。

        嵌套 dict/list 转字符串，避免 PG JSON 往返后形态变化导致 chain_valid
        误报断链（review Should）。

        Args:
            payload: 原始证据载荷。

        Returns:
            规范化后的载荷（仅 str/int/bool/None）。
        """
        return {
            str(k): v if isinstance(v, (str, int, bool)) or v is None else str(v)
            for k, v in payload.items()
        }

    @staticmethod
    def _last_chain_hash(session: Session) -> str:
        """取当前链尾 hash；空表返回空串（首行前驱）。

        Args:
            session: 当前事务的 Session（与本次 append 同一事务，读到未提交链尾）。

        Returns:
            最新一行的 chain_hash，无记录返回 ""。
        """
        tail = session.scalar(
            select(AuditEvent.chain_hash).order_by(AuditEvent.seq.desc()).limit(1)
        )
        return str(tail) if tail is not None else ""

    def append(
        self,
        user_id: str,
        session_id: str,
        action: str,
        decision: str,
        payload: AuditPayload,
        trace_id: str,
    ) -> AuditEventRow:
        """追加一条审计事件，计算 prev_hash/chain_hash 成链并落 PG。

        Args:
            user_id: 事件归属用户（成功迁移为资源 owner，DENY 为尝试者）。
            session_id: 所属会话。
            action: 动作（AuditActions 常量）。
            decision: 判定结果（OK / reason_code / FORBIDDEN）。
            payload: 证据载荷（不含 PII 原文/附件原文/代码）。
            trace_id: 链路追踪 id。

        Returns:
            写入的 AuditEventRow（含 seq / prev_hash / chain_hash / created_at）。
        """
        now = datetime.now(UTC)
        safe_payload = self._normalize_payload(payload)
        body = _canonical_body(
            user_id, session_id, action, decision, safe_payload, trace_id
        )
        with session_scope(self._factory) as session:
            # advisory lock 串行化整段「读尾→算 hash→写行」（review B4）：
            # 多实例并发 append 都读到同一链尾会断链；锁保证后写者看到前者的链尾。
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY}
            )
            prev = self._last_chain_hash(session)
            chain = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            row = AuditEvent(
                user_id=user_id,
                session_id=session_id,
                actor=user_id or _ADMIN_ACTOR,
                action=action,
                decision=decision,
                payload=dict(safe_payload),
                trace_id=trace_id,
                prev_hash=prev,
                chain_hash=chain,
                created_at=now,
            )
            session.add(row)
            session.flush()  # 取数据库生成的 seq（autoincrement）
            return self._to_row(row)

    @staticmethod
    def _to_row(row: AuditEvent) -> AuditEventRow:
        """把 ORM 行转成与内存账本一致的 AuditEventRow（不暴露 actor 字段）。

        Args:
            row: audit_event 表的一行。

        Returns:
            对外契约形态的审计行；payload 非标量值转字符串，保持 JSON 边界安全。
        """
        raw = row.payload if isinstance(row.payload, dict) else {}
        return AuditEventRow(
            seq=int(row.seq),
            user_id=str(row.user_id),
            session_id=str(row.session_id),
            action=str(row.action),
            decision=str(row.decision),
            payload=PgAuditLedger._normalize_payload(cast(AuditPayload, raw)),
            trace_id=str(row.trace_id),
            prev_hash=str(row.prev_hash),
            chain_hash=str(row.chain_hash),
            created_at=row.created_at,
        )

    def chain_valid(self) -> bool:
        """重算整条 hash 链，任一环断裂返回 False（A18 防篡改）。

        Returns:
            True 表示全库审计链完整可重放。
        """
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(AuditEvent).order_by(AuditEvent.seq.asc())
            ).all()
        prev = ""
        for row in rows:
            body = _canonical_body(
                row.user_id,
                row.session_id,
                row.action,
                row.decision,
                self._to_row(row).payload,
                row.trace_id,
            )
            expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            if row.prev_hash != prev or row.chain_hash != expected:
                return False
            prev = str(row.chain_hash)
        return True

    def by_session(self, session_id: str) -> list[AuditEventRow]:
        """按会话查审计事件（M11 重放入口，按 seq 升序）。

        Args:
            session_id: 会话标识。
        """
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(AuditEvent)
                .where(AuditEvent.session_id == session_id)
                .order_by(AuditEvent.seq.asc())
            ).all()
        return [self._to_row(row) for row in rows]

    def all(self) -> list[AuditEventRow]:
        """返回全部审计事件（按 seq 升序，供整链重放）。"""
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(AuditEvent).order_by(AuditEvent.seq.asc())
            ).all()
        return [self._to_row(row) for row in rows]
