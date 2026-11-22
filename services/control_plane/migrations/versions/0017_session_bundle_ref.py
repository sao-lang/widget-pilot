"""会话当前产物 bundle 引用 sessions.bundle_ref（ADR-009 多轮完整版）。

Revision ID: 0017_session_bundle_ref
Revises: 0016_agent_collab
Create Date: 2026-11-22

指向对象存储中当前轮的结果 bundle（含摘要/关键帧引用），供跨轮恢复时直接定位
上一轮产物。只加一个可空列；幂等守卫（inspector.has_column），downgrade 删除。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_session_bundle_ref"
down_revision = "0016_agent_collab"
branch_labels = None
depends_on = None

_TABLE: str = "sessions"
_COLUMN: str = "bundle_ref"


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    """幂等列存在性守卫（本仓 SQLAlchemy 2.1 无 has_column，按反射列名判定）。"""
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    """给 sessions 加可空 bundle_ref 列（已存在则跳过，幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE) and not _has_column(inspector, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    """回滚：删除 sessions.bundle_ref 列（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE) and _has_column(inspector, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
