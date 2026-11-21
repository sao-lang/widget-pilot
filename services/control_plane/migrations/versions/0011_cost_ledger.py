"""成本账表 cost_ledger（M07 §3.4 D / B7，计量执行留证）。

Revision ID: 0011_cost_ledger
Revises: 0010_m07_memory
Create Date: 2026-11-21

每 run 一行（run_id 唯一 → ON CONFLICT DO NOTHING 幂等），按 model_tier
分账四维用量，repro_fingerprint 含 preference_version。created_at 用
TIMESTAMPTZ 与领域模型 ``datetime`` 对齐（区别于记忆表用 Text 存 ISO 串）。
只加表，幂等守卫（inspector.has_table），downgrade 删表。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_cost_ledger"
down_revision = "0010_m07_memory"
branch_labels = None
depends_on = None

_TABLE: str = "cost_ledger"


def upgrade() -> None:
    """建 cost_ledger 表（run_id 唯一 + 归属/时序索引；已存在则跳过）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text(), nullable=False, unique=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("model_tier", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "sandbox_minutes", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("repro_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(f"ix_{_TABLE}_session_id", _TABLE, ["session_id"])
    op.create_index(f"ix_{_TABLE}_user_id", _TABLE, ["user_id"])
    op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"])


def downgrade() -> None:
    """回滚：仅删本迁移新增的 cost_ledger 表（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
