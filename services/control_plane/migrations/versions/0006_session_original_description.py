"""sessions 表加 original_description 列（R3-1，v1.8 终审）。

Revision ID: 0006_session_orig_desc
Revises: 0005_m06_telemetry
Create Date: 2026-11-21

M06 护栏前置挂起（SPEC_MISSING）发生在 run() 之前、不产生 checkpoint；
澄清重入须用首次提交原始描述恢复模型上下文，否则生成只收到规格答案。
只加一列（nullable=False + server_default=""），幂等守卫，不改动既有数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_session_orig_desc"
down_revision = "0005_m06_telemetry"
branch_labels = None
depends_on = None

_COLUMN: str = "original_description"
_TABLE: str = "sessions"


def upgrade() -> None:
    """加 original_description 列（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN not in columns:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    """删列（仅本迁移新增）。"""
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN in columns:
        op.drop_column(_TABLE, _COLUMN)
