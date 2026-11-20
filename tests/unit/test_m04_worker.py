"""M04 worker 恢复扫描单测（B1b + review B3：反查完整会话任务）。"""

from __future__ import annotations

from types import TracebackType
from typing import Self

import pytest

from packages.contracts.enums import SessionState
from services.control_plane.sessions.stores import SessionRow
from services.orchestrator.worker import scan_expired_tasks


class TestWorkerScan:
    """恢复扫描 helper：按 run_id 反查会话构造完整任务。"""

    def test_scan_returns_full_tasks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """过期 run 反查出真实 session_id/user_id；无匹配会话的 run 跳过。"""

        class _FakeResult:
            def all(self) -> list[str]:
                return ["run-a", "run-orphan", "run-b"]

        class _FakeSession:
            def scalars(self, stmt: object) -> _FakeResult:
                return _FakeResult()

            def __enter__(self) -> Self:
                return self

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc_value: BaseException | None,
                traceback: TracebackType | None,
            ) -> None:
                return None

        class _FakeSessionFactory:
            """session factory 桩：调用即产出伪 Session（供 session_scope 使用）。"""

            def __call__(self) -> _FakeSession:
                return _FakeSession()

        def fake_scope(factory: _FakeSessionFactory) -> _FakeSession:
            """fake session_scope：忽略 factory 返回伪 Session。"""
            return _FakeSession()

        def fake_find(self, run_id: str) -> SessionRow | None:
            """fake find_by_run_id：orphan run 无会话。"""
            if run_id == "run-orphan":
                return None
            return SessionRow(
                id=f"sess-{run_id}",
                user_id="u-owner",
                state=SessionState.RUNNING,
                run_id=run_id,
            )

        monkeypatch.setattr("services.orchestrator.worker.session_scope", fake_scope)
        monkeypatch.setattr(
            "services.orchestrator.worker.make_session_factory", lambda dsn: _FakeSessionFactory()
        )
        monkeypatch.setattr(
            "services.control_plane.sessions.pg_stores.PgSessionStore.find_by_run_id",
            fake_find,
        )
        tasks = scan_expired_tasks("postgresql://x")
        assert len(tasks) == 2
        assert tasks[0] == {
            "session_id": "sess-run-a",
            "user_id": "u-owner",
            "run_id": "run-a",
            "description": "",
            "resume": "1",  # M5（v1.8）：恢复任务标记，dispatcher 据此跳过校验链
        }
