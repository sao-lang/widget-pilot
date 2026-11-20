"""M05 幂等键唯一索引（N4：防并发双写两份 original）。

Revision ID: 0004_m05_idem_unique
Revises: 0003_m05_attachments
Create Date: 2026-11-21

0003 已应用到生产库，不可修改；本迁移只加唯一索引。组合 (user_id, session_id,
idem_key) 唯一——并发同键上传时第二个 INSERT 触发 IntegrityError，由服务层折叠为
复用既有行，而非写入第二条记录/第二份原文。
"""

from __future__ import annotations

from alembic import op

revision = "0004_m05_idem_unique"
down_revision = "0003_m05_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """把普通 idem_key 索引升级为三列唯一索引（先 drop 再 create）。"""
    op.drop_index("ix_attachments_idem_key", table_name="attachments")
    op.create_index(
        "uq_attachments_idem",
        "attachments",
        ["user_id", "session_id", "idem_key"],
        unique=True,
    )


def downgrade() -> None:
    """回滚：恢复普通索引。"""
    op.drop_index("uq_attachments_idem", table_name="attachments")
    op.create_index("ix_attachments_idem_key", "attachments", ["idem_key"])
