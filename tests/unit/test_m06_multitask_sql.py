"""多任务存储的 SQL 方言编译级测试（无需真 PG，ADR-011 角色层已移除）。

用假 Session 捕获 pg_stores 实际下发到 SQLAlchemy 的语句，按 PostgreSQL 方言
编译后核对：幂等 ON CONFLICT、原子 claim 等。
真实落库由 tests/integration/test_m06_multitask_pg.py 验证。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import ClauseElement

from services.orchestrator.tasks import pg_stores
from services.orchestrator.tasks.pg_stores import (
    GenerationTaskData,
    PgGenerationTaskStore,
    PgTaskTurnStore,
    TaskTurnData,
    _turn_id,
)


class _Result:
    """假执行结果（支持 first/all/scalar/rowcount）。"""

    def __init__(
        self,
        rows: Sequence[object] = (),
        *,
        rowcount: int = 0,
        scalar_value: object = None,
    ) -> None:
        self._rows = list(rows)
        self.rowcount = rowcount
        self._scalar = scalar_value

    def first(self) -> object | None:
        """首行；无行返回 None。"""
        return self._rows[0] if self._rows else None

    def all(self) -> list[object]:
        """全部行。"""
        return list(self._rows)

    def scalar(self) -> object:
        """首行首列。"""
        return self._scalar


class _Session:
    """捕获语句的假 Session（异常/结果由用例按调用顺序注入）。"""

    def __init__(self, results: Sequence[_Result]) -> None:
        self.statements: list[object] = []
        self._results = list(results)
        self._last = _Result()

    def _next(self) -> _Result:
        if self._results:
            self._last = self._results.pop(0)
        else:
            self._last = _Result()
        return self._last

    def execute(self, statement: object, params: object = None) -> _Result:
        """记录语句并按序返回结果。"""
        self.statements.append(statement)
        return self._next()

    def scalar(self, statement: object) -> object:
        """记录语句并取标量结果。"""
        self.statements.append(statement)
        return self._next().scalar()

    def scalars(self, statement: object) -> _Session:
        """记录语句；后续 .all() 从同一次结果取行。"""
        self.statements.append(statement)
        self._next()
        return self

    def all(self) -> list[object]:
        """scalars() 的链式结果。"""
        return self._last.all()


class _Capture:
    """替换 session_scope，记录每次调用打开的假 Session。"""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, queues: Sequence[Sequence[_Result]] = ()
    ) -> None:
        self.sessions: list[_Session] = []
        self._queues = iter(queues)

        @contextmanager
        def fake_scope(factory: object) -> Iterator[_Session]:
            session = _Session(next(self._queues, ()))
            self.sessions.append(session)
            yield session

        monkeypatch.setattr(pg_stores, "session_scope", fake_scope)

    @property
    def last(self) -> _Session:
        """最近一次调用的假 Session。"""
        return self.sessions[-1]


def _sql(statement: object) -> str:
    """按 PostgreSQL 方言编译语句（不内联字面量，避免 JSONB 渲染限制）。"""
    return str(cast(ClauseElement, statement).compile(dialect=postgresql.dialect()))


def _params(statement: object) -> dict[str, object]:
    """取编译后绑定参数（校验列值去向）。"""
    compiled = cast(ClauseElement, statement).compile(dialect=postgresql.dialect())
    return cast(dict[str, object], getattr(compiled, "params", {}))


def _task(index: int = 0) -> GenerationTaskData:
    """构造一条子任务数据。"""
    return GenerationTaskData(
        id=f"task-{index}",
        session_id="s1",
        parent_run_id="parent-1",
        original_description="原始描述",
        description=f"子任务 {index}",
        task_index=index,
        task_total=2,
        state="PENDING",
        attachment_ids=("att-1",),
    )


class TestGenerationTaskSQL:
    """generation_task 语句的方言编译。"""

    def test_create_many_idempotent_insert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """批量插入编译为 INSERT ... ON CONFLICT DO NOTHING，且每行字段齐全。"""
        capture = _Capture(monkeypatch)
        store = PgGenerationTaskStore("postgresql://x")
        store.create_many([_task(0), _task(1)])
        assert len(capture.sessions) == 1
        stmt = capture.last.statements[0]
        sql = _sql(stmt)
        assert "INSERT INTO generation_task" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        for column in (
            "session_id",
            "parent_run_id",
            "original_description",
            "description",
            "task_index",
            "task_total",
            "attachment_ids",
            "state",
            "current_bundle_ref",
            "last_turn_index",
        ):
            assert column in sql

    def test_create_many_empty_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空列表不开事务（避免空 INSERT  syntactic 错误）。"""
        capture = _Capture(monkeypatch)
        PgGenerationTaskStore("postgresql://x").create_many([])
        assert capture.sessions == []

    def test_claim_run_atomic_predicate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """抢占编译为 WHERE id=? AND run_id IS NULL；rowcount 决定返回值。"""
        capture = _Capture(
            monkeypatch, [[_Result(rowcount=1)], [_Result(rowcount=0)]]
        )
        store = PgGenerationTaskStore("postgresql://x")
        assert store.claim_run("task-0", "run-1") is True
        assert store.claim_run("task-0", "run-2") is False
        for session in capture.sessions:
            sql = _sql(session.statements[0])
            assert "UPDATE generation_task" in sql
            assert "run_id IS NULL" in sql

    def test_update_state_only_non_none_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只更新非 None 字段 + updated_at，未传字段不出现在 SET。"""
        capture = _Capture(monkeypatch)
        PgGenerationTaskStore("postgresql://x").update_state(
            "task-0", "DONE", result_ref="bundle://r", turn=2
        )
        sql = _sql(capture.last.statements[0])
        assert "UPDATE generation_task" in sql
        assert "state=" in sql
        assert "result_ref=" in sql
        assert "turn=" in sql
        assert "role=" not in sql
        assert "updated_at=now()" in sql
        assert "reason_code=" not in sql
        assert "evidence_ref=" not in sql

    def test_read_and_summary_sql(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get/list/summary 编译为针对本表的 SELECT（归属过滤 + 索引序）。"""
        capture = _Capture(monkeypatch, [[], [], []])
        store = PgGenerationTaskStore("postgresql://x")
        store.get("task-0")
        store.list_by_session("s1")
        store.summary("s1")
        sqls = [_sql(s.statements[0]) for s in capture.sessions]
        assert "FROM generation_task" in sqls[0]
        assert "session_id" in sqls[1]
        assert "task_index" in sqls[1]
        assert "state" in sqls[2]


class TestTaskTurnSQL:
    """task_turn 语句的方言编译。"""

    def test_append_idempotent_and_derived_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """轮次 id 由 (task_id, turn_index) 稳定派生，插入幂等。"""
        capture = _Capture(monkeypatch)
        store = PgTaskTurnStore("postgresql://x")
        store.append(
            TaskTurnData(task_id="task-0", turn_index=1, parent_run_id="parent-1")
        )
        stmt = capture.last.statements[0]
        sql = _sql(stmt)
        assert "INSERT INTO task_turn" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        assert _turn_id("task-0", 1) in _params(stmt).values()
        assert _turn_id("task-0", 1) == _turn_id("task-0", 1)
        assert _turn_id("task-0", 1) != _turn_id("task-0", 2)


