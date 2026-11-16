"""baseline: initdb 兜底（扩展已由 infra/dev/initdb/01-extensions.sql 预置）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-16

基线占位：业务表 DDL 归 M04/M11（M01 §6.5——checkpoint/side_effect 原文 SQL
作为基线迁移内容由 M04 定稿后补入）。本版本只保证版本链从空库到 head 可达。
"""
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
