"""ADR-009 会话事实摘要 ORM 模型（独立文件，避免与并行改造的 models.py 冲突）。

复用 `services.control_plane.models.Base` 声明基类；表结构对齐迁移 0014。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.agent_core.types import JsonValue
from services.control_plane.models import Base

_TzDateTime = TIMESTAMP(timezone=True)


class FactSummary(Base):
    """ADR-009 会话历史事实摘要：(session_id, turn_index) 唯一，只追加不删行。"""

    __tablename__ = "fact_summary"
    __table_args__ = (
        Index("ix_fact_summary_session_id", "session_id"),
        Index("ix_fact_summary_created_at", "created_at"),
        UniqueConstraint(
            "session_id", "turn_index", name="uq_fact_summary_session_turn"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    rendered: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
