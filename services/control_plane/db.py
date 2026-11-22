"""数据库会话管理（ORM 化，§2.1.1）。

从 DATABASE_URL 建 SQLAlchemy engine（同步驱动 pg8000）；提供 session_scope()
上下文管理器：正常退出 commit，异常回滚。每个存储操作一个短 Session，
异常不静默吞。DSN 归一化集中在此模块，禁止各存储重复实现。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_ENV_DATABASE_URL = "DATABASE_URL"
_SYNC_URL_PREFIX = "postgresql+pg8000://"
_DRIVER_PREFIXES = (
    "postgresql://",
    "postgresql+asyncpg://",
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
    "postgresql+pg8000://",
)


def sync_dsn(url: str) -> str:
    """任意 PostgreSQL DSN → SQLAlchemy pg8000 同步 DSN。

    compose 注入的是 ``postgresql+asyncpg://``，测试用 ``postgresql://``；
    两者统一收敛到同一个同步驱动，全仓不再依赖 psycopg。
    非 PostgreSQL URL 原样返回（例如本地 SQLite 测试）。
    """
    for prefix in _DRIVER_PREFIXES:
        if url.startswith(prefix):
            return _SYNC_URL_PREFIX + url[len(prefix) :]
    return url


def _default_url() -> str:
    """从环境取 DSN；缺失时回退本地开发库（测试/本地默认）。"""
    return sync_dsn(
        os.getenv(
            _ENV_DATABASE_URL,
            "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot",
        )
    )


def make_session_factory(dsn: str) -> sessionmaker[Session]:
    """按显式 DSN 建 sessionmaker（存储适配器构造时注入，契约等价原 dsn 参数）。

    Args:
        dsn: PG DSN（任意 PostgreSQL 驱动前缀自动转 pg8000 同步）。

    Returns:
        sessionmaker；expire_on_commit=False 使提交后字段仍可读（to_row 后再关闭）。
    """
    engine = create_engine(sync_dsn(dsn), pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def is_unique_violation(exc: BaseException | None) -> bool:
    """驱动无关地判定 UNIQUE 约束冲突（SQLSTATE 23505）。

    psycopg 用 ``errors.UniqueViolation``，pg8000 暴露 ``sqlstate``，
    psycopg2 暴露 ``pgcode``；统一按 SQLSTATE/类名判定，避免绑定具体驱动。
    """
    if exc is None:
        return False
    if getattr(exc, "sqlstate", None) == "23505":
        return True
    if getattr(exc, "pgcode", None) == "23505":
        return True
    # pg8000 把服务端错误字段放在首个位置参数 dict 里（'C' = SQLSTATE）。
    if exc.args and isinstance(exc.args[0], dict) and exc.args[0].get("C") == "23505":
        return True
    return type(exc).__name__ in ("UniqueViolation", "UniqueViolationError")


def default_session_factory() -> sessionmaker[Session]:
    """从环境 DATABASE_URL 建 sessionmaker（无显式 dsn 时的默认）。"""
    return make_session_factory(_default_url())


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """短 Session 上下文：正常提交、异常回滚、总是关闭。

    ORM Session 退出只 close 不 commit，故此处显式 commit，否则所有写操作静默丢失。

    Args:
        factory: 该存储实例绑定的 sessionmaker。

    Yields:
        Session，供存储适配器执行 ORM 查询/更新。
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
