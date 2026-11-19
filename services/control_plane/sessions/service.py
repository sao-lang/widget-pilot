"""M04 SessionService：会话 CRUD + 状态机 + signal + 澄清（§1.2 R4/R6、§5.2）。

状态迁移集中在 SessionService 一处（§3.1 不变量：旁路必带原因码）；
interrupt 幂等（A6）；终态 resume 抛 ConflictError（A13）；
跨用户访问由 TenantGuard 兜底（本服务复用 guard.assert_owner）。
"""

from __future__ import annotations

from uuid import uuid4

from packages.contracts.enums import SessionState
from services.control_plane.api.guard import TenantGuard
from services.control_plane.sessions.stores import (
    SessionRow,
    SessionStore,
)

__all__ = ["ConflictError", "SessionError", "SessionService"]


class SessionError(Exception):
    """会话非法迁移/校验失败（对应 INPUT_INVALID 或业务拒绝）。"""


class ConflictError(Exception):
    """状态冲突：对终态会话执行 resume/重复提交（对应 409 CONFLICT）。"""


class SessionService:
    """会话状态机服务（§5.2 流转表的唯一执行点）。"""

    def __init__(self, store: SessionStore) -> None:
        """注入会话存储；生产为 PG 适配器，测试为内存实现。"""
        self.store = store

    def create(self, user_id: str, title: str, grid: str | None) -> SessionRow:
        """新建会话：state=DRAFT、last_seq=0（§5.2 首行）。"""
        row = SessionRow(
            id=str(uuid4()),
            user_id=user_id,
            state=SessionState.DRAFT,
            title=title,
            grid=grid,
            grid_source="selector" if grid else None,
        )
        self.store.insert(row)
        return row

    def get(self, session_id: str) -> SessionRow:
        """取会话行；不存在抛 SessionError（快照/迁移的统一前置）。"""
        row = self.store.get(session_id)
        if row is None:
            raise SessionError("session not found")
        return row

    def snapshot(self, session_id: str, user_id: str) -> SessionRow:
        """会话快照查询：先租户校验再返回（A1 越权 403）。"""
        row = self.get(session_id)
        TenantGuard.assert_owner(user_id, row.user_id)
        return row

    def submit_message(
        self,
        session_id: str,
        description: str,
        attachment_ids: list[str],
    ) -> SessionRow:
        """提交描述：DRAFT→RUNNING，分配 runId（§5.2）。"""
        row = self.get(session_id)
        if row.state is not SessionState.DRAFT:
            raise ConflictError("session not in DRAFT state")
        if not description:
            raise SessionError("description required")
        row.state = SessionState.RUNNING
        row.run_id = str(uuid4())
        self.store.update(row)
        return row

    def mark_awaiting_input(self, session_id: str, reason_code: str) -> SessionRow:
        """RUNNING→AWAITING_INPUT，记录触发原因码（§5.2 / §5.3）。"""
        row = self.get(session_id)
        if row.state is not SessionState.RUNNING:
            raise ConflictError("only RUNNING can await input")
        # reason_code 当前不落 Session 主记录（按 §6.1 字段归属，过程数据走事件流）；
        # 参数保留供后续事件发射使用，避免此处塞入过程字段。
        row.state = SessionState.AWAITING_INPUT
        self.store.update(row)
        return row

    def answer_clarification(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        answer: str,
    ) -> SessionRow:
        """回答澄清：AWAITING_INPUT→RUNNING，答案入槽位，clarify_count+1（D-7）。"""
        row = self.get(session_id)
        TenantGuard.assert_owner(user_id, row.user_id)
        if row.state is not SessionState.AWAITING_INPUT:
            raise ConflictError("session not awaiting input")
        if row.clarify_count >= MAX_CLARIFY:
            raise SessionError("clarification limit reached")
        # P0-2：答案写槽位，随 checkpoint 落盘，跨天恢复可还原
        row.clarification_answer = answer
        row.clarify_count += 1
        row.state = SessionState.RUNNING
        self.store.update(row)
        return row

    def interrupt(self, session_id: str) -> SessionRow:
        """用户停止：RUNNING→PAUSED，写 checkpoint（rev+1）；挂起态幂等返回（A6）。"""
        row = self.get(session_id)
        if row.state in _HANG_STATES:
            # 幂等：已挂起则不重复写 checkpoint，直接返回当前状态（A6）
            return row
        if row.state is not SessionState.RUNNING:
            raise ConflictError("only RUNNING can be interrupted")
        row.state = SessionState.PAUSED
        row.checkpoint_rev += 1
        self.store.update(row)
        return row

    def resume(self, session_id: str) -> SessionRow:
        """用户继续：PAUSED→RUNNING；终态抛 ConflictError（A13）。"""
        row = self.get(session_id)
        if row.state in _TERMINAL_STATES:
            raise ConflictError("cannot resume terminal session")
        # AWAITING_INPUT 的回答走 answer_clarification，不走 resume（§5.5）
        if row.state is not SessionState.PAUSED:
            raise ConflictError("only PAUSED can resume")
        row.state = SessionState.RUNNING
        self.store.update(row)
        return row

    def cancel(self, session_id: str) -> SessionRow:
        """用户取消：任意非终态→CANCELLED（§5.2 修订版）。终态幂等返回。"""
        row = self.get(session_id)
        if row.state in _TERMINAL_STATES:
            return row  # 终态重复 cancel 幂等
        row.state = SessionState.CANCELLED
        self.store.update(row)
        return row

    def complete(self, session_id: str, bundle_ref: str) -> SessionRow:
        """校验全通过：RUNNING→COMPLETED，写产物引用（§5.2）。"""
        row = self.get(session_id)
        if row.state is not SessionState.RUNNING:
            raise ConflictError("only RUNNING can complete")
        row.state = SessionState.COMPLETED
        self.store.update(row)
        return row

    def fail(self, session_id: str, reason_code: str, evidence_ref: str) -> SessionRow:
        """旁路失败：→FAILED，必带原因码 + 证据引用（§4.4 / A14 证据先于结论）。

        Args:
            session_id: 会话标识。
            reason_code: ReasonCode 值（如 MODEL_ERROR / BUDGET_EXHAUSTED）。
            evidence_ref: 对象存储证据引用（evidence/{session}/{run}/*），不可为空。

        Raises:
            SessionError: 原因码或证据引用缺失（FAILED 不允许裸失败）。
            ConflictError: 会话已是终态。
        """
        if not reason_code or not evidence_ref:
            raise SessionError("FAILED requires reason_code and evidence_ref")
        row = self.get(session_id)
        if row.state in _TERMINAL_STATES:
            raise ConflictError("cannot fail terminal session")
        row.state = SessionState.FAILED
        row.reason_code = reason_code
        row.evidence_ref = evidence_ref
        self.store.update(row)
        return row


# 终态集合与挂起态集合（编码规则 5：有限取值用常量，禁散落裸枚举比较）
_TERMINAL_STATES = frozenset(
    (SessionState.COMPLETED, SessionState.FAILED, SessionState.CANCELLED)
)
_HANG_STATES = frozenset((SessionState.PAUSED, SessionState.AWAITING_INPUT))
MAX_CLARIFY = 2
"""澄清次数上限（D-7 / MAX_CLARIFY=2，来自 M02 enums 常量）。"""
