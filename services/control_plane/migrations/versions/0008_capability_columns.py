"""能力披露日志列（ADR-003 / Q1：扩 M06 两表承载 capability_id/level）。

Revision ID: 0008_capability_columns
Revises: 0007_guard_hit
Create Date: 2026-11-21

给 need_skill_log 加 capability_id / level 列，给 skill_prefetch_log 加
capability_kind 列；均为 nullable 以兼容存量行，新写入使用新列。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_capability_columns"
down_revision = "0007_guard_hit"
branch_labels = None
depends_on = None

_TABLE_NEED_SKILL = "need_skill_log"
_TABLE_SKILL_PREFETCH = "skill_prefetch_log"


def _add_column_if_missing(table: str, column: str, col_type: sa.types.TypeEngine) -> None:
    """列不存在时才添加，保证迁移幂等（重复运行不报错）。"""
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column not in existing:
        op.add_column(table, sa.Column(column, col_type, nullable=True))


def upgrade() -> None:
    """扩两表列（幂等）。"""
    _add_column_if_missing(_TABLE_NEED_SKILL, "capability_id", sa.Text())
    _add_column_if_missing(_TABLE_NEED_SKILL, "level", sa.Integer())
    _add_column_if_missing(_TABLE_SKILL_PREFETCH, "capability_kind", sa.Text())


def downgrade() -> None:
    """回滚删除新增列（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    for table, column in (
        (_TABLE_NEED_SKILL, "capability_id"),
        (_TABLE_NEED_SKILL, "level"),
        (_TABLE_SKILL_PREFETCH, "capability_kind"),
    ):
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            op.drop_column(table, column)
