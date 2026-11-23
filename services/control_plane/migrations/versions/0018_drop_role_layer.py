"""移除多 agent 角色层：drop agent_message / agent_workspace 表 + generation_task.role 列（ADR-011）。

Revision ID: 0018_drop_role_layer
Revises: 0017_session_bundle_ref
Create Date: 2026-11-23

ADR-011 把多任务下的多 agent 回退为单 agent：角色层的消息留证表与黑板表不再
需要，generation_task.role 列（当前执行角色）随之移除。turn 列保留——它是
ADR-009 任务层迭代语义，不属于角色层。幂等守卫用 get_columns()（本仓 SQLAlchemy
2.1 无 Inspector.has_column）。downgrade 重建表结构（列定义对齐 0016）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0018_drop_role_layer"
down_revision = "0017_session_bundle_ref"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    """幂等列存在性守卫（本仓 SQLAlchemy 2.1 无 has_column，按反射列名判定）。"""
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    """drop 两张角色层表 + generation_task.role 列（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    for table in ("agent_message", "agent_workspace"):
        if inspector.has_table(table):
            op.drop_table(table)
    if inspector.has_table("generation_task") and _has_column(inspector, "generation_task", "role"):
        op.drop_column("generation_task", "role")


def downgrade() -> None:
    """回滚：重建两张表与 generation_task.role 列（列定义对齐迁移 0016）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("generation_task") and not _has_column(inspector, "generation_task", "role"):
        op.add_column("generation_task", sa.Column("role", sa.Text(), nullable=True))
    if not inspector.has_table("agent_message"):
        op.create_table(
            "agent_message",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("task_id", sa.Text(), nullable=False),
            sa.Column("agent_id", sa.Text(), nullable=False),
            sa.Column("from_role", sa.Text(), nullable=False),
            sa.Column("to_role", sa.Text(), nullable=False),
            sa.Column("message_type", sa.Text(), nullable=False),
            sa.Column("trace_id", sa.Text(), nullable=False),
            sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("rev", sa.Integer(), nullable=False),
            sa.Column("prev_hash", sa.Text(), nullable=False),
            sa.Column("hash", sa.Text(), nullable=False),
            sa.Column("turn", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_agent_message_session_task", "agent_message", ["session_id", "task_id"])
        op.create_index("ix_agent_message_task_rev", "agent_message", ["task_id", "rev"])
    if not inspector.has_table("agent_workspace"):
        op.create_table(
            "agent_workspace",
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("key", sa.Text(), nullable=False),
            sa.Column("value_ref", sa.Text(), nullable=False),
            sa.Column("rev", sa.Integer(), nullable=False),
            sa.Column("writer_role", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("session_id", "key", name="pk_agent_workspace"),
        )
