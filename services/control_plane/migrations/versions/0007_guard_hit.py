"""guard_hit 表：命中留证 + prev_hash hash 链（runbook §2 定稿，2026-11-21 补齐）。

Revision ID: 0007_guard_hit
Revises: 0006_session_orig_desc
Create Date: 2026-11-21

字段按 M06 §6.1 实体表：id / session_id / run_id / hit_type / rule_id /
judgement / excerpt / stage / decision / trace_id / prev_hash / row_hash /
should_pass / created_at。前四留证字段 NOT NULL，stage 固定 INPUT，
prev_hash + row_hash 构成防篡改链。只加表，幂等守卫。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_guard_hit"
down_revision = "0006_session_orig_desc"
branch_labels = None
depends_on = None

_TABLE: str = "guard_hit"


def upgrade() -> None:
    """建 guard_hit 表 + 归属/时序索引（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("hit_type", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("judgement", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column(
            "stage", sa.Text(), nullable=False, server_default="INPUT"
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("row_hash", sa.Text(), nullable=False),
        sa.Column("should_pass", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(f"ix_{_TABLE}_session_id", _TABLE, ["session_id"])
    op.create_index(f"ix_{_TABLE}_trace_id", _TABLE, ["trace_id"])
    op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"])


def downgrade() -> None:
    """回滚：仅删本迁移新增的 guard_hit 表。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
