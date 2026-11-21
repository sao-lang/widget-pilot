"""删除技能预取日志表（ADR-003 后续决策：纯按需加载，不留兼容层）。

Revision ID: 0009_drop_skill_prefetch
Revises: 0008_capability_columns
Create Date: 2026-11-21

技能预取（(widget_type,grid) 静态映射 → recipe_ids）在 ADR-003 后已无消费者：
规程正文由模型经 LOAD_CAPABILITY 从 L0 目录按需加载，预取结果不再下发。
本迁移 drop 迁移 0005 建立、0008 扩列过的 skill_prefetch_log 表，幂等。
need_skill_log 保留（能力请求的审计写入方仍在用）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_drop_skill_prefetch"
down_revision = "0008_capability_columns"
branch_labels = None
depends_on = None

_TABLE_SKILL_PREFETCH = "skill_prefetch_log"


def upgrade() -> None:
    """删除技能预取日志表（幂等）。"""
    if sa.inspect(op.get_bind()).has_table(_TABLE_SKILL_PREFETCH):
        op.drop_table(_TABLE_SKILL_PREFETCH)


def downgrade() -> None:
    """回滚重建空表（历史数据不可恢复；预取已废弃，不尝试恢复）。"""
    if sa.inspect(op.get_bind()).has_table(_TABLE_SKILL_PREFETCH):
        return
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("capability_kind", sa.Text(), nullable=True),  # 0008 加的列，回滚时一并恢复
    )
    op.create_index(f"ix_{_TABLE_SKILL_PREFETCH}_session_id", _TABLE_SKILL_PREFETCH, ["session_id"])
    op.create_index(f"ix_{_TABLE_SKILL_PREFETCH}_created_at", _TABLE_SKILL_PREFETCH, ["created_at"])
