"""M04 业务表：users / sessions / checkpoint / side_effect / audit_event。

Revision ID: 0002_m04_tables
Revises: 0001_baseline
Create Date: 2026-11-19

字段来源：M04 §6.1 实体字段表、§3.4 checkpoint/side_effect SQL、
01 基线 §1.1/§1.2。v1.4 新增字段：Session.grid 可空 + grid_source/
clarify_count/clarification_answer（ADR-001 D-6/D-7）、
failure 的 reason_code/evidence_ref（§4.4 A14）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_m04_tables"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建 M04 五张业务表（幂等由 alembic 版本链保证）。"""
    # users：账号与配额归属（§6.1 User；citext 唯一邮箱）
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
        sa.Column(
            "share_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("quota", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # sessions：会话主记录（§6.1 Session，v1.4 字段全集）
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("grid", sa.Text(), nullable=True),  # v1.3 D-6：可空，缺省询问
        sa.Column("grid_source", sa.Text(), nullable=True),
        sa.Column("clarify_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clarification_answer", sa.Text(), nullable=True),  # v1.4 P0-2 落点
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("checkpoint_rev", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.Text(), nullable=True),  # §4.4 A14
        sa.Column("evidence_ref", sa.Text(), nullable=True),  # §4.4 A14
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # checkpoint：版本化状态 + hash 链 + 租约（§3.4 SQL 原文；PK (run_id, rev)）
    op.create_table(
        "checkpoint",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("node", sa.Text(), nullable=False),
        sa.Column("state_ref", sa.Text(), nullable=False),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("prev_hash", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("run_id", "rev"),
    )
    # 恢复扫描：lease_until < now() 的索引（§11.4 step 3）
    op.create_index("ix_checkpoint_lease_until", "checkpoint", ["lease_until"])

    # side_effect：幂等副作用表（§3.4 SQL；idem_key = session|step|payload_hash）
    op.create_table(
        "side_effect",
        sa.Column("idem_key", sa.Text(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # audit_event：只追加审计 hash 链（§6.1 AuditEvent）
    op.create_table(
        "audit_event",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False, server_default=""),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("prev_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("chain_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_event_session_id", "audit_event", ["session_id"])


def downgrade() -> None:
    """回滚 M04 五张表（逆序 drop）。"""
    op.drop_index("ix_audit_event_session_id", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_table("side_effect")
    op.drop_index("ix_checkpoint_lease_until", table_name="checkpoint")
    op.drop_table("checkpoint")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
