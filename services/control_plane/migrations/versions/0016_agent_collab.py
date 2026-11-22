"""多 Agent 协作表：task_turn / agent_message / agent_workspace（ADR-007/009）。

Revision ID: 0016_agent_collab
Revises: 0015_generation_tasks
Create Date: 2026-11-22

task_turn 每 (task_id, turn_index) 一行，记录该轮用户意图、事实摘要与产物 bundle
引用（ADR-009 多轮完整版）。agent_message 只追加，prev_hash + hash 构成防篡改链
（与 guard_hit 同纪律），写入按 session advisory lock 串行化。agent_workspace 是
版本化黑板，只存对象存储引用，写走 UPDATE ... WHERE rev = ? 乐观锁。

只加表，幂等守卫（inspector.has_table），downgrade 删表。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_agent_collab"
down_revision = "0015_generation_tasks"
branch_labels = None
depends_on = None

_TABLE_TURN: str = "task_turn"
_TABLE_MESSAGE: str = "agent_message"
_TABLE_WORKSPACE: str = "agent_workspace"


def _create_turn() -> None:
    """建 task_turn 表 + 唯一/索引。"""
    op.create_table(
        _TABLE_TURN,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("parent_run_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column(
            "user_intent", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("fact_summary_ref", sa.Text(), nullable=True),
        sa.Column("result_bundle_ref", sa.Text(), nullable=True),
        sa.Column("cost_ref", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("task_id", "turn_index", name="uq_task_turn_task_turn"),
    )
    op.create_index(f"ix_{_TABLE_TURN}_parent_run_id", _TABLE_TURN, ["parent_run_id"])


def _create_message() -> None:
    """建 agent_message 表 + 归属/版本索引。"""
    op.create_table(
        _TABLE_MESSAGE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("from_role", sa.Text(), nullable=False),
        sa.Column("to_role", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        f"ix_{_TABLE_MESSAGE}_session_task", _TABLE_MESSAGE, ["session_id", "task_id"]
    )
    op.create_index(f"ix_{_TABLE_MESSAGE}_task_rev", _TABLE_MESSAGE, ["task_id", "rev"])


def _create_workspace() -> None:
    """建 agent_workspace 表（复合主键即乐观锁定位键）。"""
    op.create_table(
        _TABLE_WORKSPACE,
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value_ref", sa.Text(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("writer_role", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("session_id", "key", name="pk_agent_workspace"),
    )


def upgrade() -> None:
    """建三张协作表（已存在则跳过，幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE_TURN):
        _create_turn()
    if not inspector.has_table(_TABLE_MESSAGE):
        _create_message()
    if not inspector.has_table(_TABLE_WORKSPACE):
        _create_workspace()


def downgrade() -> None:
    """回滚：仅删本迁移新增的三张表（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    for table in (_TABLE_WORKSPACE, _TABLE_MESSAGE, _TABLE_TURN):
        if inspector.has_table(table):
            op.drop_table(table)
