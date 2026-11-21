"""能力披露日志列（ADR-003）——历史占位迁移，现为 no-op。

Revision ID: 0008_capability_columns
Revises: 0007_guard_hit
Create Date: 2026-11-21

历史：本迁移曾给 need_skill_log 加 capability_id/level、给 skill_prefetch_log 加
capability_kind。按用户决策"同步改写历史迁移"：
- 0005 已直接建 capability_audit_log（capability_id/level 一步到位），无需再加列；
- skill_prefetch_log 整套已删（0009 drop），capability_kind 列随之消失。
本 revision 保留以维持迁移链完整，upgrade/downgrade 均为空操作。
"""
from __future__ import annotations

revision = "0008_capability_columns"
down_revision = "0007_guard_hit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """no-op：能力列已在 0005 一步到位，预取表已由 0009 删除。"""
    return


def downgrade() -> None:
    """no-op：无本迁移引入的变更可回滚。"""
    return
