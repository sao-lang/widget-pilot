"""会话历史事实摘要表 fact_summary（ADR-009 多轮对话 A 方案）。

Revision ID: 0014_fact_summary
Revises: 0011_cost_ledger
Create Date: 2026-11-22

每 (session_id, turn_index) 一行——零 LLM 抽取的结构化短事实；只追加不删行，
写并发由 advisory xact lock 串行化（同 M07 记忆纪律）。只加表，幂等守卫
（inspector.has_table），downgrade 删表。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_fact_summary"
down_revision = "0011_cost_ledger"
branch_labels = None
depends_on = None

_TABLE: str = "fact_summary"


def upgrade() -> None:
    """建 fact_summary 表（session+turn 唯一 + 时序索引；已存在则跳过）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("rendered", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_fact_summary_session_turn"),
    )
    op.create_index(f"ix_{_TABLE}_session_id", _TABLE, ["session_id"])
    op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"])


def downgrade() -> None:
    """回滚：仅删本迁移新增的 fact_summary 表（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
