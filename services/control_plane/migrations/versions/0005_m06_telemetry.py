"""M06 只追加日志三表：intent_route_log / skill_prefetch_log / need_skill_log。

Revision ID: 0005_m06_telemetry
Revises: 0004_m05_idem_unique
Create Date: 2026-11-21

迁移只加表，不改动/删除既有表。字段逐项对齐 M06 telemetry.py 的 Row dataclass，
session_id 为归属键（与 M04/M05 一致建索引），created_at 供时序检索。
upgrade 以 has_table 守卫，重复执行安全（幂等）。
"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from alembic import op

revision = "0005_m06_telemetry"
down_revision = "0004_m05_idem_unique"
branch_labels = None
depends_on = None

_TABLE_INTENT_ROUTE: Final[str] = "intent_route_log"
_TABLE_SKILL_PREFETCH: Final[str] = "skill_prefetch_log"
_TABLE_NEED_SKILL: Final[str] = "need_skill_log"


def upgrade() -> None:
    """建三张只追加日志表 + session_id/created_at 索引（幂等）。"""
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table(_TABLE_INTENT_ROUTE):
        op.create_table(
            _TABLE_INTENT_ROUTE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("route_layer", sa.Text(), nullable=False),
            sa.Column("intent", sa.Text(), nullable=False),
            sa.Column("similarity", sa.Float(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("samples_version", sa.Text(), nullable=False),
            sa.Column("trace_id", sa.Text(), nullable=False, server_default=""),
            sa.Column("fallback_reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            f"ix_{_TABLE_INTENT_ROUTE}_session_id", _TABLE_INTENT_ROUTE, ["session_id"]
        )
        op.create_index(
            f"ix_{_TABLE_INTENT_ROUTE}_created_at", _TABLE_INTENT_ROUTE, ["created_at"]
        )

    if not inspector.has_table(_TABLE_SKILL_PREFETCH):
        op.create_table(
            _TABLE_SKILL_PREFETCH,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("widget_type", sa.Text(), nullable=False),
            sa.Column("grid", sa.Text(), nullable=False),
            sa.Column("recipe_ids", sa.Text(), nullable=False),
            sa.Column("hit", sa.Boolean(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("trace_id", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            f"ix_{_TABLE_SKILL_PREFETCH}_session_id",
            _TABLE_SKILL_PREFETCH,
            ["session_id"],
        )
        op.create_index(
            f"ix_{_TABLE_SKILL_PREFETCH}_created_at",
            _TABLE_SKILL_PREFETCH,
            ["created_at"],
        )

    if not inspector.has_table(_TABLE_NEED_SKILL):
        op.create_table(
            _TABLE_NEED_SKILL,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("recipe_id", sa.Text(), nullable=False),
            sa.Column("depth", sa.Integer(), nullable=False),
            sa.Column("turn_count", sa.Integer(), nullable=False),
            sa.Column("tokens", sa.Integer(), nullable=False),
            sa.Column("decision", sa.Text(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("trace_id", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            f"ix_{_TABLE_NEED_SKILL}_session_id", _TABLE_NEED_SKILL, ["session_id"]
        )
        op.create_index(
            f"ix_{_TABLE_NEED_SKILL}_created_at", _TABLE_NEED_SKILL, ["created_at"]
        )


def downgrade() -> None:
    """回滚：删三张日志表（仅本迁移新增的表，不触碰既有表）。"""
    inspector = sa.inspect(op.get_bind())
    for table in (_TABLE_INTENT_ROUTE, _TABLE_SKILL_PREFETCH, _TABLE_NEED_SKILL):
        if inspector.has_table(table):
            op.drop_table(table)
