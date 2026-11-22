"""多任务规划表 generation_task（ADR-006 多任务 / 多轮）。

Revision ID: 0015_generation_tasks
Revises: 0014_fact_summary
Create Date: 2026-11-22

每 (parent_run_id, task_index) 一行子任务：id = task_id（uuid5 稳定派生），
run_id 在子任务首次被 worker 消费时原子抢占（NULL → 具体 run）。关联状态机值
PENDING/QUEUED/RUNNING/VALIDATING/DONE/AWAITING_INPUT/FAILED/CANCELLED 存 Text
（与 tasks/states.py TaskState 对齐），不进 M02 契约枚举。

同迁移给 guard_hit / audit_event 追加可空 task_id 归属列（幂等 has_column 守卫），
使护栏留证可归因到具体子任务。只加表/列，不改既有列；downgrade 逆向移除。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_generation_tasks"
down_revision = "0014_fact_summary"
branch_labels = None
depends_on = None

_TABLE: str = "generation_task"
_RELATED_TABLES: tuple[str, ...] = ("guard_hit", "audit_event")


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    """幂等列存在性守卫（本仓 SQLAlchemy 2.1 无 has_column，按反射列名判定）。"""
    return any(item["name"] == column for item in inspector.get_columns(table))


def _add_task_id_columns(inspector: sa.Inspector) -> None:
    """给关联留证表加可空 task_id（已存在则跳过，幂等）。"""
    for table in _RELATED_TABLES:
        if inspector.has_table(table) and not _has_column(inspector, table, "task_id"):
            op.add_column(table, sa.Column("task_id", sa.Text(), nullable=True))


def upgrade() -> None:
    """建 generation_task 表 + 索引 + 留证表 task_id 列（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("parent_run_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=True, unique=True),
            sa.Column("original_description", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("task_index", sa.Integer(), nullable=False),
            sa.Column("task_total", sa.Integer(), nullable=False),
            sa.Column("grid", sa.Text(), nullable=True),
            sa.Column(
                "attachment_ids",
                JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("state", sa.Text(), nullable=False),
            sa.Column("result_ref", sa.Text(), nullable=True),
            sa.Column("plan_trace_ref", sa.Text(), nullable=True),
            sa.Column("reason_code", sa.Text(), nullable=True),
            sa.Column("evidence_ref", sa.Text(), nullable=True),
            sa.Column("role", sa.Text(), nullable=True),
            sa.Column(
                "turn", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("current_bundle_ref", sa.Text(), nullable=True),
            sa.Column(
                "last_turn_index", sa.Integer(), nullable=False, server_default="0"
            ),
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
            sa.UniqueConstraint(
                "parent_run_id", "task_index", name="uq_generation_task_parent_index"
            ),
        )
        op.create_index(f"ix_{_TABLE}_session_id", _TABLE, ["session_id"])
        op.create_index(
            f"ix_{_TABLE}_session_state", _TABLE, ["session_id", "state"]
        )
    _add_task_id_columns(inspector)


def downgrade() -> None:
    """回滚：删 task_id 列 + 本迁移表（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    for table in _RELATED_TABLES:
        if inspector.has_table(table) and _has_column(inspector, table, "task_id"):
            op.drop_column(table, "task_id")
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
