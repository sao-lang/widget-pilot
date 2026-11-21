"""M07 记忆三表：语义记忆 / 情节记忆 / 轻量程序记忆（ADR-002 D-9~D-14）。

Revision ID: 0010_m07_memory
Revises: 0009_drop_skill_prefetch
Create Date: 2026-11-21

- ``user_preference``：user_id 主键 + fields JSONB（每字段证据化）+ version。
- ``preference_event``：只追加、BIGSERIAL id、row_hash 链、失效不删行；
  时间戳列用 Text 存 UTC ISO 串，与领域模型 ``created_at: str`` 逐字一致。
- ``user_procedure``：user_id + recipe_id 联合主键，采纳计数归零失效不删行。

三表均幂等守卫（``inspector.has_table``），重复执行不报错。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_m07_memory"
down_revision = "0009_drop_skill_prefetch"
branch_labels = None
depends_on = None

_TABLE_PREFERENCE: str = "user_preference"
_TABLE_EVENT: str = "preference_event"
_TABLE_PROCEDURE: str = "user_procedure"


def upgrade() -> None:
    """建三张记忆表与归属/时序索引（已存在则跳过，幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE_PREFERENCE):
        op.create_table(
            _TABLE_PREFERENCE,
            sa.Column("user_id", sa.Text(), primary_key=True),
            sa.Column("fields", JSONB(), nullable=False, server_default="{}"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    if not inspector.has_table(_TABLE_EVENT):
        op.create_table(
            _TABLE_EVENT,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column("session_id", sa.Text(), nullable=True),
            sa.Column("field_name", sa.Text(), nullable=False),
            sa.Column("old_value", JSONB(), nullable=True),
            sa.Column("new_value", JSONB(), nullable=True),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("source_weight", sa.Float(), nullable=False),
            sa.Column("decision", sa.Text(), nullable=False),
            sa.Column("trace_id", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("superseded_at", sa.Text(), nullable=True),
            sa.Column("revoked_at", sa.Text(), nullable=True),
            sa.Column("row_hash", sa.Text(), nullable=False),
        )
        op.create_index(
            f"ix_{_TABLE_EVENT}_user_id_field",
            _TABLE_EVENT,
            ["user_id", "field_name"],
        )
        op.create_index(f"ix_{_TABLE_EVENT}_row_hash", _TABLE_EVENT, ["row_hash"])
        op.create_index(f"ix_{_TABLE_EVENT}_created_at", _TABLE_EVENT, ["created_at"])
    if not inspector.has_table(_TABLE_PROCEDURE):
        op.create_table(
            _TABLE_PROCEDURE,
            sa.Column("user_id", sa.Text(), nullable=False, primary_key=True),
            sa.Column("recipe_id", sa.Text(), nullable=False, primary_key=True),
            sa.Column("widget_type", sa.Text(), nullable=False),
            sa.Column(
                "adopt_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("last_adopted_at", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    """回滚：仅删本迁移新增的三张表（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    for table in (_TABLE_PROCEDURE, _TABLE_EVENT, _TABLE_PREFERENCE):
        if inspector.has_table(table):
            op.drop_table(table)
