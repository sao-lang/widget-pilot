"""M04 会话与状态机单测（§8.2 A6/A13 + §5.2 流转表）。

覆盖：会话 CRUD / DRAFT→RUNNING / interrupt 幂等 / resume / cancel /
澄清回注 / 终态保护 / 跨用户快照隔离。状态迁移集中在 SessionService。
"""

from __future__ import annotations

import pytest

from packages.contracts.enums import SessionState
from services.control_plane.api.guard import ForbiddenError
from services.control_plane.sessions.service import (
    ConflictError,
    SessionError,
    SessionService,
)
from tests.support import AuditLedger, InMemorySessionStore


@pytest.fixture
def sessions() -> SessionService:
    """独立会话服务：内存存储，不依赖 auth（租户隔离单独测）。"""
    return SessionService(InMemorySessionStore(), audit=AuditLedger())


def _new_session(sessions: SessionService, owner: str = "user-a") -> str:
    """便捷：创建 DRAFT 会话返回 id。"""
    return sessions.create(user_id=owner, title="test", grid="4x4").id


class TestSessionCRUD:
    """会话创建与快照（R4 / §4.1 sessions 端点）。"""

    def test_create_starts_draft(self, sessions: SessionService) -> None:
        """新建会话 state=DRAFT、last_seq=0（§5.2 流转表）。"""
        s = sessions.create(user_id="user-a", title="t", grid="4x4")
        assert s.state is SessionState.DRAFT
        assert s.last_seq == 0
        assert s.grid == "4x4"

    def test_submit_message_moves_to_running(self, sessions: SessionService) -> None:
        """提交描述：DRAFT→RUNNING，分配 runId（§5.2）。"""
        sid = _new_session(sessions)
        result = sessions.submit_message(sid, "user-a", "做一个日历", [])
        assert result.state is SessionState.RUNNING
        assert result.run_id

    def test_snapshot_cross_user_forbidden(self, sessions: SessionService) -> None:
        """用户 B 读用户 A 会话快照抛 ForbiddenError（A1）。"""
        sid = _new_session(sessions, owner="user-a")
        with pytest.raises(ForbiddenError):
            sessions.snapshot(session_id=sid, user_id="user-b")


class TestSignals:
    """interrupt / resume / cancel（R6 / §5.2 / §8.2 A6/A13）。"""

    def test_interrupt_moves_to_paused(self, sessions: SessionService) -> None:
        """RUNNING→PAUSED（用户停止）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        assert sessions.interrupt(sid, "user-a").state is SessionState.PAUSED

    def test_interrupt_is_idempotent(self, sessions: SessionService) -> None:
        """连续两次 interrupt 状态仍 PAUSED、checkpoint rev 只 +1（A6）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        first = sessions.interrupt(sid, "user-a")
        second = sessions.interrupt(sid, "user-a")
        assert first.state is SessionState.PAUSED
        assert second.state is SessionState.PAUSED
        assert (
            second.checkpoint_rev == first.checkpoint_rev
        )  # 幂等：不重复写 checkpoint

    def test_resume_from_paused(self, sessions: SessionService) -> None:
        """PAUSED→RUNNING（用户继续，按 runId+seq 恢复）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        sessions.interrupt(sid, "user-a")
        assert sessions.resume(sid, "user-a").state is SessionState.RUNNING

    def test_cancel_from_any_non_terminal(self, sessions: SessionService) -> None:
        """任意非终态可 cancel → CANCELLED（§5.2 修订后流转表）。"""
        for setup in ("draft", "running", "paused", "awaiting"):
            sid = _new_session(sessions)
            if setup in ("running", "paused", "awaiting"):
                sessions.submit_message(sid, "user-a", "desc", [])
            if setup == "paused":
                sessions.interrupt(sid, "user-a")
            if setup == "awaiting":
                sessions.mark_awaiting_input(sid, "user-a", "SPEC_MISSING")
            result = sessions.cancel(sid, "user-a")
            assert result.state is SessionState.CANCELLED, setup

    def test_resume_terminal_returns_conflict(self, sessions: SessionService) -> None:
        """COMPLETED/CANCELLED 上 resume 抛 ConflictError（A13：409）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        sessions.cancel(sid, "user-a")
        with pytest.raises(ConflictError):
            sessions.resume(sid, "user-a")

    def test_failure_carries_reason_and_evidence(
        self, sessions: SessionService
    ) -> None:
        """FAILED 必须带 code + evidenceRef（A14：证据先于结论，§4.4）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        failed = sessions.fail(sid, "MODEL_ERROR", "evidence/s1/run-1/error.json")
        assert failed.state is SessionState.FAILED
        assert failed.reason_code == "MODEL_ERROR"
        assert failed.evidence_ref == "evidence/s1/run-1/error.json"


class TestClarification:
    """澄清回注（D-7 / §4.1 clarifications / P0-2 落点）。"""

    def test_awaiting_then_answer_resumes(self, sessions: SessionService) -> None:
        """RUNNING→AWAITING_INPUT→回答→RUNNING，clarify_count +1（§5.2）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        sessions.mark_awaiting_input(sid, "user-a", "CLARIFICATION_REQUIRED")
        assert sessions.get(sid).state is SessionState.AWAITING_INPUT
        result = sessions.answer_clarification(sid, "user-a", "q-1", "用 4x4")
        assert result.state is SessionState.RUNNING
        assert result.clarify_count == 1

    def test_clarification_answer_persisted(self, sessions: SessionService) -> None:
        """澄清答案写入 clarification_answer 槽位（P0-2：跨天恢复可还原）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        sessions.mark_awaiting_input(sid, "user-a", "ASK_USER")
        sessions.answer_clarification(sid, "user-a", "q-1", "选第二个选项")
        assert sessions.get(sid).clarification_answer == "选第二个选项"

    def test_clarify_count_limit_enforced(self, sessions: SessionService) -> None:
        """clarify_count 上限 2，第 3 次回答抛 SessionError（D-7 ≤2 次）。"""
        sid = _new_session(sessions)
        sessions.submit_message(sid, "user-a", "desc", [])
        for i in range(2):
            sessions.mark_awaiting_input(sid, "user-a", "ASK_USER")
            sessions.answer_clarification(sid, "user-a", f"q-{i}", "answer")
        sessions.mark_awaiting_input(sid, "user-a", "ASK_USER")
        with pytest.raises(SessionError):
            sessions.answer_clarification(sid, "user-a", "q-3", "answer")
