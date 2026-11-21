"""ADR-003 未完成项3：新列 INSERT 语句结构正确（无需真实 PG）。

用 monkeypatch 捕获 psycopg.connect 执行的 SQL 与参数，断言列名/占位符/参数
三者一致。真实落库仍需 Docker PG（登记为待真实环境验证）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from services.guardrails import pg_stores
from services.guardrails.telemetry import NeedSkillLogRow
from services.guardrails.types import HitDecision


class _Conn:
    """捕获 execute 调用的假连接。"""

    def __init__(self, calls: list[tuple[str, tuple[object, ...]]]) -> None:
        self.calls = calls

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.calls.append((sql, params))


def _capture(monkeypatch, calls):  # type: ignore[no-untyped-def]
    """替换 psycopg.connect 为上下文管理器形式的假连接。"""

    @contextmanager
    def fake_connect(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield _Conn(calls)

    monkeypatch.setattr(pg_stores.psycopg, "connect", fake_connect)


def test_need_skill_insert_includes_new_columns(monkeypatch):
    """need_skill_log INSERT 含 capability_id/level 且参数对齐（11 列/11 值）。"""
    calls: list[tuple[str, tuple[object, ...]]] = []
    _capture(monkeypatch, calls)
    store = pg_stores.PgNeedSkillLogStore("postgresql://x")
    store.append(
        NeedSkillLogRow(
            "s", "r", "recipe.calendar.month.v3", 2, 1, 220, HitDecision.REJECT,
            "DENIED", "t1", capability_id="recipe.calendar.month.v3", level=2,
        )
    )
    assert len(calls) == 1
    sql, params = calls[0]
    assert "capability_id, level" in sql
    assert sql.count("%s") == len(params) == 11
    assert params[-2] == "recipe.calendar.month.v3"
    assert params[-1] == 2
