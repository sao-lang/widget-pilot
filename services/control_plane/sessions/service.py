"""M04 SessionService：会话 CRUD + 状态机 + signal + 澄清（§1.2 R4/R6、§5.2）。

状态迁移集中在 SessionService 一处（§3.1 不变量：旁路必带原因码）；
interrupt 幂等（A6）；终态 resume 抛 ConflictError（A13）；
跨用户访问由 TenantGuard 兜底（本服务复用 guard.assert_owner）。
"""

from __future__ import annotations

from uuid import uuid4

from packages.contracts import enums as _contracts_enums
from packages.contracts.enums import SessionState
from services.control_plane.api.guard import ForbiddenError, TenantGuard
from services.control_plane.audit.ledger import AuditActions, AuditLedgerPort
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

    def __init__(self, store: SessionStore, audit: AuditLedgerPort) -> None:
        """注入会话存储与审计账本（S1：R10 迁移留痕）。

        Args:
            store: 会话存储（生产为 PG）。
            audit: 审计账本；生产为 PgAuditLedger，测试传入 tests/support 的替身。
        """
        self.store = store
        self.audit = audit

    def _commit(self, row: SessionRow, expected_state: SessionState) -> None:
        """带乐观锁的写回；存储层冲突（ValueError）折叠为领域 ConflictError（409）。"""
        try:
            self.store.update(row, expected_state)
        except ValueError as exc:
            raise ConflictError("state changed by another request") from exc

    def _audit(
        self, row: SessionRow, action: str, decision: str = "OK", trace_id: str = ""
    ) -> None:
        """S1：把一次状态迁移/拒绝写入只追加审计账本（M04 产生，M11 汇总）。"""
        self.audit.append(
            user_id=row.user_id,
            session_id=row.id,
            action=action,
            decision=decision,
            payload={"state": row.state.value, "rev": row.checkpoint_rev},
            trace_id=trace_id,
        )

    def _assert_owner(self, user_id: str, row: SessionRow) -> None:
        """租户校验 + S1 收尾：拒绝时写 DENY 审计（不泄漏资源内容）再抛 ForbiddenError。"""
        try:
            TenantGuard.assert_owner(user_id, row.user_id)
        except ForbiddenError:
            self.audit.append(
                user_id=user_id,  # 尝试者（非资源 owner，便于 M11 溯源）
                session_id=row.id,
                action=AuditActions.DENY,
                decision="FORBIDDEN",
                payload={"actor": user_id},  # 仅尝试者 id，不含资源内容
                trace_id="",
            )
            raise

    def create(self, user_id: str, title: str, grid: str | None) -> SessionRow:
        """新建会话：state=DRAFT、last_seq=0（§5.2 首行）。

        Args:
            user_id: 归属用户（服务端从 JWT 解析，不信前端声明）。
            title: 会话标题，可空。
            grid: 规格网格（如 4x4），空表示用户未选；有值时 grid_source=selector。

        Returns:
            新建的 SessionRow（id 服务端生成）。
        """
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
        """取会话行；不存在抛 SessionError（快照/迁移的统一前置）。

        Args:
            session_id: 会话标识。

        Returns:
            会话行副本（内存实现返回 replace 副本，避免调用方改到存储内对象）。

        Raises:
            SessionError: 会话不存在。
        """
        row = self.store.get(session_id)
        if row is None:
            raise SessionError("session not found")
        return row

    def snapshot(self, session_id: str, user_id: str) -> SessionRow:
        """会话快照查询：先租户校验再返回（A1 越权 403）。

        Args:
            session_id: 会话标识。
            user_id: 请求者（JWT claims）；与会话 owner 不等则抛 ForbiddenError。

        Returns:
            归属校验通过的会话行。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        return row

    def submit_message(
        self,
        session_id: str,
        user_id: str,
        description: str,
        attachment_ids: list[str],
    ) -> SessionRow:
        """提交描述：DRAFT|COMPLETED→RUNNING，分配 runId（§5.2/ADR-009）。B1：先租户校验。

        ADR-009 v0.2：COMPLETED 不再是不可逆终态——同一会话可再提交控件修改消息，
        分配新 run_id（parent_run_id 语义），旧轮次事实由 FactSummary 承接。

        Args:
            session_id: 会话标识。
            user_id: 请求者；经 _assert_owner 校验归属。
            description: 用户对 widget 的描述，不可为空。
            attachment_ids: 关联附件 id 列表（本模块只持有 id，不解析内容）。

        Returns:
            迁移后的会话行（state=RUNNING，run_id 新分配）。

        Raises:
            ForbiddenError: 非会话 owner。
            ConflictError: 会话非 DRAFT/COMPLETED 态。
            SessionError: description 为空。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        if row.state not in (SessionState.DRAFT, SessionState.COMPLETED):
            raise ConflictError("session not in DRAFT/COMPLETED state")
        if not description:
            raise SessionError("description required")
        before_state = row.state
        row.state = SessionState.RUNNING
        row.run_id = str(uuid4())
        # R3-1（v1.8）：持久化原始描述——M06 护栏前置挂起（SPEC_MISSING 等）
        # 发生在 run() 之前、不产生 checkpoint；澄清重入须据此恢复模型上下文。
        row.original_description = description
        self._commit(row, before_state)
        self._audit(row, AuditActions.STATE_TRANSITION)
        return row

    def mark_awaiting_input(
        self,
        session_id: str,
        user_id: str,
        reason_code: str,
        evidence_ref: str | None = None,
    ) -> SessionRow:
        """RUNNING→AWAITING_INPUT，记录触发原因码（§5.2 / §5.3）。B1：先租户校验。

        Args:
            session_id: 会话标识。
            user_id: 请求者；经 _assert_owner 校验归属。
            reason_code: 触发原因（如 SPEC_MISSING / ASK_USER），落审计 decision 字段。
            evidence_ref: 触发挂起的输入快照证据引用（A4，v1.8 接线）；
                M06 ValidationResult.evidence_ref（带 sha256），供前端回放为何挂起。
                可选以兼容模型 ask_user 等非护栏挂起来源（无快照时留空）。

        Raises:
            ForbiddenError: 非会话 owner。
            ConflictError: 会话非 RUNNING 态。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        if row.state is not SessionState.RUNNING:
            raise ConflictError("only RUNNING can await input")
        # N2：reason_code 不落 Session 主记录（按 §6.1 字段归属，过程数据走事件流），
        # 但写入审计 payload（SPEC_MISSING vs ask_user 可区分，供 M11 重放）。
        # A4（v1.8）：evidence_ref 落 Session 主记录，与 fail 对齐——挂起也须可回放证据。
        before_state = row.state
        row.state = SessionState.AWAITING_INPUT
        row.evidence_ref = evidence_ref
        self._commit(row, before_state)
        self.audit.append(
            user_id=row.user_id,
            session_id=row.id,
            action=AuditActions.STATE_TRANSITION,
            decision=reason_code,  # 触发原因码落审计决策字段
            payload={
                "state": row.state.value,
                "reason_code": reason_code,
                "evidence_ref": evidence_ref or "",
            },
            trace_id="",
        )
        return row

    def answer_clarification(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        answer: str,
    ) -> SessionRow:
        """回答澄清：AWAITING_INPUT→RUNNING，答案入槽位，clarify_count+1（D-7）。

        Args:
            session_id: 会话标识。
            user_id: 请求者；经 _assert_owner 校验归属。
            question_id: 澄清问题 id（过程标识，不入 Session 主记录）。
            answer: 用户回答，写入 clarification_answer 槽位随 checkpoint 落盘。

        Raises:
            ForbiddenError: 非会话 owner。
            ConflictError: 会话非 AWAITING_INPUT 态。
            SessionError: clarify_count 已达上限（MAX_CLARIFY=2）。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        if row.state is not SessionState.AWAITING_INPUT:
            raise ConflictError("session not awaiting input")
        if row.clarify_count >= MAX_CLARIFY:
            raise SessionError("clarification limit reached")
        # P0-2：答案写槽位，随 checkpoint 落盘，跨天恢复可还原
        before_state = row.state
        row.clarification_answer = answer
        row.clarify_count += 1
        row.state = SessionState.RUNNING
        self._commit(row, before_state)
        return row

    def interrupt(self, session_id: str, user_id: str) -> SessionRow:
        """用户停止：RUNNING→PAUSED，写 checkpoint（rev+1）；挂起态幂等返回（A6）。B1：先租户校验。

        Args:
            session_id: 会话标识。
            user_id: 请求者；经 _assert_owner 校验归属。

        Returns:
            迁移后的会话行；若已处于挂起态则原样返回、不重复写 checkpoint（A6）。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        if row.state in _HANG_STATES:
            # 幂等：已挂起则不重复写 checkpoint，直接返回当前状态（A6）
            return row
        if row.state is not SessionState.RUNNING:
            raise ConflictError("only RUNNING can be interrupted")
        before_state = row.state
        row.state = SessionState.PAUSED
        row.checkpoint_rev += 1
        self._commit(row, before_state)
        self._audit(row, AuditActions.INTERRUPT)
        return row

    def resume(self, session_id: str, user_id: str) -> SessionRow:
        """用户继续：PAUSED→RUNNING；终态抛 ConflictError（A13）。B1：先租户校验。

        Args:
            session_id: 会话标识。
            user_id: 请求者；经 _assert_owner 校验归属。

        Raises:
            ForbiddenError: 非会话 owner。
            ConflictError: 会话为终态，或非 PAUSED（AWAITING_INPUT 走 answer_clarification）。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        if row.state in _TERMINAL_STATES:
            raise ConflictError("cannot resume terminal session")
        # AWAITING_INPUT 的回答走 answer_clarification，不走 resume（§5.5）
        if row.state is not SessionState.PAUSED:
            raise ConflictError("only PAUSED can resume")
        before_state = row.state
        row.state = SessionState.RUNNING
        self._commit(row, before_state)
        self._audit(row, AuditActions.RESUME)
        return row

    def cancel(self, session_id: str, user_id: str) -> SessionRow:
        """用户取消：任意非终态→CANCELLED（§5.2 修订版）。终态幂等返回。B1：先租户校验。

        Args:
            session_id: 会话标识。
            user_id: 请求者；经 _assert_owner 校验归属。

        Returns:
            迁移后的会话行；已终态则原样返回（cancel 幂等）。
        """
        row = self.get(session_id)
        self._assert_owner(user_id, row)
        if row.state in _TERMINAL_STATES:
            return row  # 终态重复 cancel 幂等
        before_state = row.state
        row.state = SessionState.CANCELLED
        self._commit(row, before_state)
        self._audit(row, AuditActions.CANCEL)
        return row

    def complete(self, session_id: str, bundle_ref: str) -> SessionRow:
        """校验全通过：RUNNING→COMPLETED，写产物引用（§5.2）。

        内部方法：由 M08 校验通过后调用，不经用户 REST 端点，故无 user_id 参数。

        Args:
            session_id: 会话标识。
            bundle_ref: 产物包引用（校验通过的最终产物）。

        Raises:
            ConflictError: 会话非 RUNNING 态。
        """
        row = self.get(session_id)
        if row.state is not SessionState.RUNNING:
            raise ConflictError("only RUNNING can complete")
        before_state = row.state
        row.state = SessionState.COMPLETED
        # ADR-009 完整版 / B4 缺口修复：真正把产物引用落到 SessionRow，
        # 供下一轮迭代读取上一版 bundle（旧版不删，task_turn 逐轮留痕）。
        row.bundle_ref = bundle_ref
        self._commit(row, before_state)
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
        before_state = row.state
        row.state = SessionState.FAILED
        row.reason_code = reason_code
        row.evidence_ref = evidence_ref
        self._commit(row, before_state)
        self._audit(row, AuditActions.STATE_TRANSITION, decision=reason_code)
        return row


# 终态集合与挂起态集合（编码规则 5：有限取值用常量，禁散落裸枚举比较）
_TERMINAL_STATES = frozenset(
    (SessionState.COMPLETED, SessionState.FAILED, SessionState.CANCELLED)
)
_HANG_STATES = frozenset((SessionState.PAUSED, SessionState.AWAITING_INPUT))
# N1：单一事实来源——从 M02 enums 常量导入（不再本地重复定义）
MAX_CLARIFY = _contracts_enums.MAX_CLARIFY
"""澄清次数上限（D-7，值来自 M02 enums 单一来源）。"""
