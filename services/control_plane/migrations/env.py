"""Alembic env：DATABASE_URL 为唯一配置来源（12-factor，M01 §4.3）。"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL", "")
# asyncpg URL → alembic 同步驱动 psycopg 兼容处理（迁移用同步连接）
if database_url.startswith("postgresql+asyncpg://"):
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
if not database_url:
    database_url = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = None  # 业务表 DDL 归 M04/M11；基线迁移为手工编写


def run_migrations_offline() -> None:
    context.configure(url=database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
