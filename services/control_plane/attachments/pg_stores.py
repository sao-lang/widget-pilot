"""M05 PG 版附件索引（AttachmentStore Protocol 的生产实现，§6.1 / 迁移 0003）。

实现 AttachmentStorePort 协议（编码规则 8/9）；租户隔离键 user_id
贯穿 insert/get/list/update，越权由 AttachmentService 的 TenantGuard 挡住。
"""

from __future__ import annotations

from sqlalchemy import select, update

from services.control_plane.attachments.stores import AttachmentRow
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import Attachment


class PgAttachmentStore:
    """PG attachments 表：实现与内存版一致的方法集合（ORM）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN，构造绑定的 sessionmaker。"""
        self._factory = make_session_factory(dsn)

    def insert(self, row: AttachmentRow) -> None:
        """插入附件行（幂等由 idem_key 索引 + 上层查询保证）。"""
        obj = Attachment(
            id=row.id,
            user_id=row.user_id,
            session_id=row.session_id,
            kind=row.kind,
            mime=row.mime,
            size=row.size,
            object_key=row.object_key,
            hash=row.hash,
            role=row.role,
            role_confidence=row.role_confidence,
            insight_ref=row.insight_ref,
            insight_quality=row.insight_quality,
            handler=row.handler,
            processing_state=row.processing_state,
            clarify_hint=row.clarify_hint,
            idem_key=row.idem_key,
            evidence_ref=row.evidence_ref,
        )
        with session_scope(self._factory) as session:
            session.add(obj)

    def get(self, attachment_id: str) -> AttachmentRow | None:
        """按 id 取附件行；无则 None。"""
        with session_scope(self._factory) as session:
            obj = session.scalar(
                select(Attachment).where(Attachment.id == attachment_id)
            )
        return obj.to_row() if obj is not None else None

    def list_by_session(self, user_id: str, session_id: str) -> list[AttachmentRow]:
        """租户隔离查询：仅该用户该会话的附件。"""
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(Attachment).where(
                    Attachment.user_id == user_id,
                    Attachment.session_id == session_id,
                )
            ).all()
        return [r.to_row() for r in rows]

    def update(self, row: AttachmentRow) -> None:
        """写回全字段（按 id UPDATE，不含乐观锁——role 纠偏由上层幂等保证）。"""
        with session_scope(self._factory) as session:
            session.execute(
                update(Attachment)
                .where(Attachment.id == row.id)
                .values(
                    user_id=row.user_id,
                    session_id=row.session_id,
                    kind=row.kind,
                    mime=row.mime,
                    size=row.size,
                    object_key=row.object_key,
                    hash=row.hash,
                    role=row.role,
                    role_confidence=row.role_confidence,
                    insight_ref=row.insight_ref,
                    insight_quality=row.insight_quality,
                    handler=row.handler,
                    processing_state=row.processing_state,
                    clarify_hint=row.clarify_hint,
                    idem_key=row.idem_key,
                    evidence_ref=row.evidence_ref,
                )
            )

    def all_rows(self) -> list[AttachmentRow]:
        """列出全部附件行（红线扫描/统计用）。"""
        with session_scope(self._factory) as session:
            rows = session.scalars(select(Attachment)).all()
        return [r.to_row() for r in rows]
