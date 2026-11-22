"""集成测试的 PG 访问助手（统一走 SQLAlchemy + pg8000，不依赖具体 DBAPI）。

测试仍可用接近 psycopg 风格的 ``%s`` 占位符写 SQL；助手把它转成 SQLAlchemy
``text()`` 的命名参数，保持原有集成断言可读性的同时，全仓不再安装 psycopg。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, Final

from sqlalchemy import Connection, NullPool, create_engine, text
from sqlalchemy.engine import CursorResult, Row

from services.control_plane.db import sync_dsn

DEFAULT_PG_DSN: Final[str] = (
    "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
)
_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"%s")


@contextmanager
def pg_connection(
    dsn: str = DEFAULT_PG_DSN, *, connect_timeout: int = 2
) -> Iterator[Connection]:
    """开一个短连接，退出时提交并释放连接池。"""
    engine = create_engine(
        sync_dsn(dsn), connect_args={"timeout": connect_timeout}, poolclass=NullPool
    )
    try:
        with engine.connect() as conn:
            yield conn
            conn.commit()
    finally:
        engine.dispose()


def pg_available(dsn: str = DEFAULT_PG_DSN) -> bool:
    """探测 PG 可达性；用于集成用例的 skip 守卫。"""
    try:
        with pg_connection(dsn):
            return True
    except Exception:  # noqa: BLE001 - 连通性探测必须吞任意错以决定 skip
        return False


def _statement(sql: str, params: Iterable[object]) -> tuple[Any, dict[str, object]]:
    """把 ``%s`` 占位符转成 SQLAlchemy 命名参数。"""
    values = tuple(params)
    index = iter(range(len(values)))
    translated = _PLACEHOLDER.sub(lambda _: f":p{next(index)}", sql)
    return text(translated), {f"p{i}": value for i, value in enumerate(values)}


def pg_execute(
    conn: Connection, sql: str, params: Iterable[object] = ()
) -> CursorResult[Any]:
    """执行一段测试 SQL（``%s`` 占位符由助手翻译）。"""
    stmt, bound = _statement(sql, params)
    return conn.execute(stmt, bound)


def pg_fetchone(
    conn: Connection, sql: str, params: Iterable[object] = ()
) -> Row | None:
    """执行并取首行。"""
    return pg_execute(conn, sql, params).first()


def pg_fetchall(
    conn: Connection, sql: str, params: Iterable[object] = ()
) -> list[Row]:
    """执行并取全部行。"""
    return list(pg_execute(conn, sql, params).all())


def pg_scalar(conn: Connection, sql: str, params: Iterable[object] = ()) -> object:
    """执行并取首行首列。"""
    return pg_execute(conn, sql, params).scalar()


def pg_truncate(
    conn: Connection,
    tables: Iterable[str],
    *,
    restart_identity: bool = False,
    cascade: bool = False,
) -> None:
    """清空给定表（测试隔离用；表名来自测试常量，不接受外部输入）。"""
    suffix = ""
    if restart_identity:
        suffix += " RESTART IDENTITY"
    if cascade:
        suffix += " CASCADE"
    pg_execute(conn, f"TRUNCATE TABLE {', '.join(tables)}{suffix}")
