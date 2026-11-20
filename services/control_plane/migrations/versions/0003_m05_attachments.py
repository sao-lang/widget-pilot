"""M05 附件表：attachments（§6.1 持久化实体字段表）。

Revision ID: 0003_m05_attachments
Revises: 0002_m04_tables
Create Date: 2026-11-20

字段来源：M05 §6.1（v1.7 S1 拆双 confidence）。原文/洞察/派生件为大对象，
只存对象存储 key 引用（§6.5），不把二进制塞进 PG。租户隔离键 user_id 建索引。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_m05_attachments"
down_revision = "0002_m04_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建 attachments 表（§6.1 字段，hash/双 confidence/租户索引）。"""
    op.create_table(
        "attachments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("role_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("insight_ref", sa.Text(), nullable=True),
        sa.Column("insight_quality", sa.Float(), nullable=False, server_default="0"),
        sa.Column("handler", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "processing_state", sa.Text(), nullable=False, server_default="RECEIVED"
        ),
        sa.Column("clarify_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column("idem_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_attachments_user_id", "attachments", ["user_id"])
    op.create_index("ix_attachments_session_id", "attachments", ["session_id"])
    op.create_index("ix_attachments_idem_key", "attachments", ["idem_key"])


def downgrade() -> None:
    """回滚 attachments 表（逆序 drop 索引 + 表）。"""
    op.drop_index("ix_attachments_idem_key", table_name="attachments")
    op.drop_index("ix_attachments_session_id", table_name="attachments")
    op.drop_index("ix_attachments_user_id", table_name="attachments")
    op.drop_table("attachments")
