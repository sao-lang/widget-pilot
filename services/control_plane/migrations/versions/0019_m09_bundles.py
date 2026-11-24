"""M09 产物索引 + 版本链 + M07 采纳证据 bundle_ref 锚点（FR-12 / M09 §9.0）。

三件事：① 建 widget_bundle 索引表（大对象只存引用，不入库）；
② 建 bundle_version 版本链表（git 式 parent 指针，回滚沿用编译产物）；
③ 给 preference_event 加 bundle_ref 列（M09 删除/回滚精确撤销采纳证据锚点）。
全部幂等守卫（get_columns / has_table），downgrade 逆序删。
Revision ID 长度 <=32（alembic_version varchar(32) 约束）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019_m09_bundles"
down_revision = "0018_drop_role_layer"
branch_labels = None
depends_on = None

_BUNDLE = "widget_bundle"
_VERSION = "bundle_version"
_PREF_EVENT = "preference_event"
_COLUMN = "bundle_ref"


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    """幂等列存在性守卫（本仓 SQLAlchemy 2.1 无 has_column，按反射列名判定）。"""
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    """建两张表 + 加一列（均幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_BUNDLE):
        op.create_table(
            _BUNDLE,
            sa.Column("bundle_id", sa.Text(), primary_key=True),
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("spec_ref", sa.Text(), nullable=False),
            sa.Column("source_ref", sa.Text(), nullable=False),
            sa.Column("compiled_ref", sa.Text(), nullable=False),
            sa.Column("manifest", JSONB(), nullable=False, server_default="{}"),
            sa.Column("static_check", JSONB(), nullable=False, server_default="{}"),
            sa.Column("sandbox_test", JSONB(), nullable=False, server_default="{}"),
            sa.Column("state", sa.Text(), nullable=False, server_default="GENERATED"),
            sa.Column("hash", sa.Text(), nullable=False),
            sa.Column("versions", JSONB(), nullable=False, server_default="[]"),
            sa.Column("evidence_ref", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_widget_bundle_user_id", _BUNDLE, ["user_id"])
        op.create_index("ix_widget_bundle_session_id", _BUNDLE, ["session_id"])
        op.create_index("ix_widget_bundle_state", _BUNDLE, ["state"])
    if not inspector.has_table(_VERSION):
        op.create_table(
            _VERSION,
            sa.Column("version_id", sa.Text(), primary_key=True),
            sa.Column("bundle_id", sa.Text(), nullable=False),
            sa.Column("parent_id", sa.Text(), nullable=True),
            sa.Column("compiled_ref", sa.Text(), nullable=False),
            sa.Column("compiled_hash", sa.Text(), nullable=False),
            sa.Column("manifest_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
        op.create_index("ix_bundle_version_bundle_id", _VERSION, ["bundle_id"])
    if inspector.has_table(_PREF_EVENT) and not _has_column(
        inspector, _PREF_EVENT, _COLUMN
    ):
        op.add_column(
            _PREF_EVENT,
            sa.Column(_COLUMN, sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    """逆序回滚（幂等）。"""
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_PREF_EVENT) and _has_column(inspector, _PREF_EVENT, _COLUMN):
        op.drop_column(_PREF_EVENT, _COLUMN)
    if inspector.has_table(_VERSION):
        op.drop_table(_VERSION)
    if inspector.has_table(_BUNDLE):
        op.drop_table(_BUNDLE)
