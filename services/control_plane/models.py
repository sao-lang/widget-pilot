"""M04 数据访问层 ORM 模型（v1.5：五张业务表的声明式映射，§2.1.1）。

字段逐列对齐迁移 0002（schema 单一来源）；每列显式声明 server_default，
否则 session.add() 会把未赋值属性写成 NULL、绕过 DB 默认值触发 NOT NULL 违约。

to_row() 把 ORM 实例收窄为 dataclass（UserRow/SessionRow）：
- Protocol 契约要求返回 dataclass（内存实现同构，规则 8/9）；
- 集中 fail-safe 容错（未知 role/state 回退安全成员 —— 规则 5；JSON/时间收窄 —— 规则 6）；
- 避免 Session 关闭后读惰性属性触发 DetachedInstanceError。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, Float, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from packages.contracts.enums import SessionState, UserRole
from packages.contracts.types import JsonObject
from services.control_plane.accounts.stores import UserRow
from services.control_plane.attachments.stores import AttachmentRow
from services.control_plane.sessions.stores import SessionRow

# 时区感知时间戳（对齐迁移的 sa.DateTime(timezone=True)）
_TzDateTime = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    """声明式基类（所有 M04 ORM 模型的共同元数据容器）。"""


def _as_int(value: int | str | None) -> int:
    """把 DB 数值列收窄为 int（异常/None 回退 0，fail-safe）。"""
    return int(value) if isinstance(value, (int, str)) else 0


def _as_datetime(value: datetime | str | None) -> datetime:
    """把 DB 时间列收窄为 aware datetime（异常回退当前 UTC，fail-safe）。"""
    return value if isinstance(value, datetime) else datetime.now(UTC)


class User(Base):
    """users 表：账号与配额归属（§6.1 User，对齐 0002 users 表）。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    """账号标识（uuid，服务端生成）。"""
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    """登录名（应用层 lower() 保证大小写不敏感，迁移为 Text+unique）。"""
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    """argon2/bcrypt 哈希，不存明文。"""
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    """角色（user/curator，D-3）。"""
    share_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )
    """是否允许产物进公共语料（D-1）。"""
    quota: Mapped[dict[str, int]] = mapped_column(
        JSON, nullable=False, server_default="{}"
    )
    """配额归属（JSON，M11 消费）。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""

    def to_row(self) -> UserRow:
        """ORM 实例 → UserRow dataclass（集中容错，规则 5/6）。"""
        try:
            role = UserRole(str(self.role))
        except ValueError:
            role = UserRole.USER  # 未知角色回退安全成员
        quota = self.quota if isinstance(self.quota, dict) else {}
        return UserRow(
            id=str(self.id),
            email=str(self.email),
            password_hash=str(self.password_hash),
            role=role,
            share_enabled=bool(self.share_enabled),
            quota=quota,
            created_at=_as_datetime(self.created_at),
        )


class Session(Base):
    """sessions 表：会话主记录（§6.1 Session，对齐 0002 sessions 表）。"""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    """会话标识（uuid）。"""
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    """租户隔离键。"""
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=SessionState.DRAFT.value
    )
    """状态机当前态。"""
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """会话标题。"""
    grid: Mapped[str | None] = mapped_column(Text, nullable=True)
    """可选规格（v1.3 D-6 可空）。"""
    grid_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    """规格来源溯源。"""
    clarify_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    """已回答澄清次数（上限 2，D-7）。"""
    clarification_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    """澄清答案槽位（P0-2 跨天恢复）。"""
    original_description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    """首次提交原始描述（R3-1 v1.8）：护栏前置挂起无 checkpoint，澄清重入据此恢复上下文。"""
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    """SSE 重连锚点（单调）。"""
    checkpoint_rev: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    """checkpoint 版本（interrupt 幂等断言点）。"""
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    """执行记录标识（恢复锚点）。"""
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    """FAILED 原因码（§4.4 A14）。"""
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """FAILED 证据引用（§4.4 A14）。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""
    updated_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """更新时间（UTC）。"""

    def to_row(self) -> SessionRow:
        """ORM 实例 → SessionRow dataclass（集中容错，规则 5/6）。"""
        try:
            state = SessionState(str(self.state))
        except ValueError:
            state = SessionState.DRAFT  # 未知状态回退安全成员
        return SessionRow(
            id=str(self.id),
            user_id=str(self.user_id),
            state=state,
            title=str(self.title or ""),
            grid=self.grid if isinstance(self.grid, str) else None,
            grid_source=self.grid_source if isinstance(self.grid_source, str) else None,
            clarify_count=_as_int(self.clarify_count),
            clarification_answer=self.clarification_answer
            if isinstance(self.clarification_answer, str)
            else None,
            original_description=str(self.original_description or ""),
            last_seq=_as_int(self.last_seq),
            checkpoint_rev=_as_int(self.checkpoint_rev),
            run_id=self.run_id if isinstance(self.run_id, str) else None,
            reason_code=self.reason_code if isinstance(self.reason_code, str) else None,
            evidence_ref=self.evidence_ref
            if isinstance(self.evidence_ref, str)
            else None,
            created_at=_as_datetime(self.created_at),
            updated_at=_as_datetime(self.updated_at),
        )


class Checkpoint(Base):
    """checkpoint 表：状态快照 + run 级租约（对齐 0002 checkpoint 表）。"""

    __tablename__ = "checkpoint"
    __table_args__ = (Index("ix_checkpoint_lease_until", "lease_until"),)

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    """运行标识（复合主键 1/2）。"""
    rev: Mapped[int] = mapped_column(Integer, primary_key=True)
    """单调版本（复合主键 2/2）。"""
    node: Mapped[str] = mapped_column(Text, nullable=False)
    """触发落盘的决策节点。"""
    state_ref: Mapped[str] = mapped_column(Text, nullable=False)
    """对象存储引用。"""
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    """持锁 worker 标识（B2：run 级，随 checkpoint 继承）。"""
    lease_until: Mapped[datetime | None] = mapped_column(_TzDateTime, nullable=True)
    """租约到期时间（原子抢锁判定列）。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """hash 链前一环。"""


class SideEffect(Base):
    """side_effect 表：副作用幂等占位（对齐 0002 side_effect 表）。

    当前幂等走 Redis SETNX；此模型保留表映射供未来切换（设计 §2.1.1 五表齐全）。
    """

    __tablename__ = "side_effect"

    idem_key: Mapped[str] = mapped_column(Text, primary_key=True)
    """幂等键（session_id + step + payload_hash）。"""
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    """运行标识。"""
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """副作用产物引用。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""


class AuditEvent(Base):
    """audit_event 表：只追加审计 hash 链（对齐 0002 audit_event 表）。"""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_session_id", "session_id"),)

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    """只追加序号。"""
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    """归属用户（成功=owner，DENY=尝试者）。"""
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    """所属会话。"""
    actor: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """操作者标识。"""
    action: Mapped[str] = mapped_column(Text, nullable=False)
    """动作（AuditActions 常量）。"""
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    """判定结果（OK / reason_code / FORBIDDEN）。"""
    payload: Mapped[JsonObject] = mapped_column(
        JSON, nullable=False, server_default="{}"
    )
    """证据载荷（无 PII 原文）。"""
    trace_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """链路追踪 id。"""
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """链上前一条 hash。"""
    chain_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """本条链 hash（全字段入链，S6）。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """写入时间（UTC）。"""


class Attachment(Base):
    """attachments 表：M05 附件索引（§6.1，迁移 0003）。

    大对象（原文/洞察/关键帧）只存对象存储 key 引用；本表只放索引、
    hash 与 v1.7 拆分的双 confidence。user_id 为租户隔离键。
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_user_id", "user_id"),
        Index("ix_attachments_session_id", "session_id"),
        Index("uq_attachments_idem", "user_id", "session_id", "idem_key", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    """附件标识（uuid）。"""
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    """租户隔离键。"""
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    """所属会话。"""
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    """附件类别（image/video/text）。"""
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    """嗅探确认后的真实 mime。"""
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """字节数。"""
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    """原文对象 key（att/*/original）。"""
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    """原文 sha256（读时校验）。"""
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    """由描述判定的 role；歧义时 NULL。"""
    role_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    """role 判定置信度（M06 据此判明确性，S1）。"""
    insight_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """AttachmentInsight 引用（带 hash）。"""
    insight_quality: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    """处理结果质量（不参与 role 判定，S1）。"""
    handler: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """处理器名（静态注册白名单）。"""
    processing_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="RECEIVED"
    )
    """附件处理状态机（§5.1）。"""
    clarify_hint: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """歧义澄清建议。"""
    idem_key: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """上传幂等键（内容 + session + description）。"""
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """失败证据引用。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""

    def to_row(self) -> AttachmentRow:
        """ORM 实例 → AttachmentRow dataclass（集中容错，规则 5/6）。"""
        return AttachmentRow(
            id=str(self.id),
            user_id=str(self.user_id),
            session_id=str(self.session_id),
            kind=str(self.kind),
            mime=str(self.mime),
            size=_as_int(self.size),
            object_key=str(self.object_key),
            hash=str(self.hash),
            role=self.role if isinstance(self.role, str) else None,
            role_confidence=float(self.role_confidence or 0),
            insight_ref=self.insight_ref if isinstance(self.insight_ref, str) else None,
            insight_quality=float(self.insight_quality or 0),
            handler=str(self.handler or ""),
            processing_state=str(self.processing_state or "RECEIVED"),
            clarify_hint=str(self.clarify_hint or ""),
            idem_key=str(self.idem_key or ""),
            evidence_ref=str(self.evidence_ref or ""),
        )
