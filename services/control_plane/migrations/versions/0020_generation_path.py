"""ADR-019：generation_path 可观测性（成本账加列 + 分流决策 occurrence 表）。

① cost_ledger 加 generation_path 列（server_default=llm-only，老行自动落默认值）；
② 建 generation_occurrence 轻量事件表，记录每次分流决策（含 clarification/网关
off/通道异常这些不落成本账的非终态分支），供全漏斗统计。

幂等守卫（has_table / get_columns），downgrade 逆序删。
Revision ID 长度 <=32（alembic_version varchar(32) 约束）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_generation_path"
down_revision = "0019_m09_bundles"
branch_labels = None
depends_on = None

_LEDGER = "cost_ledger"
_OCCURRENCE = "generation_occurrence"
_COLUMN = "generation_path"


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    """幂等列存在性守卫（本仓 SQLAlchemy 2.1 无 has_column）。"""
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    """加列 + 建表（均幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_LEDGER) and not _has_column(inspector, _LEDGER, _COLUMN):
        op.add_column(
            _LEDGER,
            sa.Column(_COLUMN, sa.Text(), nullable=False, server_default="llm-only"),
        )
    if not inspector.has_table(_OCCURRENCE):
        op.create_table(
            _OCCURRENCE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("generation_path", sa.Text(), nullable=False),
            sa.Column("branch", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_gen_occ_session_id", _OCCURRENCE, ["session_id"])
        op.create_index("ix_gen_occ_path", _OCCURRENCE, ["generation_path"])


def downgrade() -> None:
    """逆序删表 + 删列（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_OCCURRENCE):
        op.drop_index("ix_gen_occ_path", table_name=_OCCURRENCE)
        op.drop_index("ix_gen_occ_session_id", table_name=_OCCURRENCE)
        op.drop_table(_OCCURRENCE)
    if inspector.has_table(_LEDGER) and _has_column(inspector, _LEDGER, _COLUMN):
        op.drop_column(_LEDGER, _COLUMN)
