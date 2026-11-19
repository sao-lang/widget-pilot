"""M04 数据库会话管理（v1.5 ORM 化，§2.1.1）。

从 DATABASE_URL 建 SQLAlchemy engine（同步驱动 psycopg）；提供 get_session()
上下文管理器：正常退出 commit（对齐原 psycopg `with connect()` 的自动提交语义），
异常回滚。每个存储操作一个短 Session，异常不静默吞。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_ENV_DATABASE_URL = "DATABASE_URL"
_ASYNCPG_PREFIX = "postgresql+asyncpg://"
_SYNC_PREFIX = "postgresql://"


def _sync_dsn(url: str) -> str:
    """asyncpg DSN → psycopg 同步 DSN（ORM 用同步 Session）。"""
    if url.startswith(_ASYNCPG_PREFIX):
        return url.replace(_ASYNCPG_PREFIX, _SYNC_PREFIX, 1)
    return url


def _default_url() -> str:
    """从环境取 DSN；缺失时回退本地开发库（测试/本地默认）。"""
    return _sync_dsn(
        os.getenv(
            _ENV_DATABASE_URL,
            "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot",
        )
    )


def make_session_factory(dsn: str) -> sessionmaker[Session]:
    """按显式 DSN 建 sessionmaker（存储适配器构造时注入，契约等价原 dsn 参数）。

    Args:
        dsn: PG DSN（asyncpg 前缀自动转 psycopg 同步）。

    Returns:
        sessionmaker；expire_on_commit=False 使提交后字段仍可读（to_row 后再关闭）。
    """
    engine = create_engine(_sync_dsn(dsn), pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def default_session_factory() -> sessionmaker[Session]:
    """从环境 DATABASE_URL 建 sessionmaker（无显式 dsn 时的默认）。"""
    return make_session_factory(_default_url())


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """短 Session 上下文：正常提交、异常回滚、总是关闭。

    等价于原 psycopg `with psycopg.connect()` 的连接生命周期——ORM Session 退出
    只 close 不 commit，故此处显式 commit，否则所有写操作静默丢失。

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
