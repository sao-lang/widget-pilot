"""测试替身（test doubles）：M04 控制面内存实现，生产代码禁止 import。

实现从 services/control_plane 原样迁移，仅保留中文 docstring 与行为；
数据类/枚举/协议（UserRow、SessionRow、SSEEvent、AuditEventRow、AuditPayload）
从生产模块导入，避免重复定义导致类型漂移。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import FastAPI, Header, HTTPException, status

from packages.agent_core.types import JsonValue
from packages.contracts.enums import ReasonCode, SessionState
from packages.contracts.types import ApiEnvelope

if TYPE_CHECKING:
    from services.orchestrator.dispatcher import Task
from services.control_plane.accounts.service import AuthError, AuthService
from services.control_plane.accounts.stores import (
    DuplicateEmailError,
    UserRow,
    UserStore,
)
from services.control_plane.attachments.ports import AttachmentServicePort
from services.control_plane.audit.ledger import (
    AuditEventRow,
    AuditLedgerPort,
    AuditPayload,
    _canonical_body,
)
from services.control_plane.sessions.service import ConflictError, SessionService
from services.control_plane.sessions.sse import EventWindow, SSEEvent
from services.control_plane.sessions.stores import SessionRow, SessionStore

__all__ = [
    "AuditEventRow",
    "AuditLedger",
    "AuditPayload",
    "DuplicateEmailError",
    "InMemorySessionStore",
    "InMemoryUserStore",
    "SSEEvent",
    "SSEWindow",
    "SessionRow",
    "SessionState",
    "SpySessionService",
    "build_test_app",
    "install_test_awaiting_route",
]


class InMemoryUserStore:
    """内存用户表：按 email 与 id 双索引，唯一性由注册路径保证。"""

    def __init__(self) -> None:
        """初始化空表与双索引。"""
        self._by_email: dict[str, UserRow] = {}
        self._by_id: dict[str, UserRow] = {}

    def insert(self, row: UserRow) -> None:
        """插入用户；email 重复抛 DuplicateEmailError（对齐 PG UNIQUE 约束）。"""
        if row.email in self._by_email:
            raise DuplicateEmailError(row.email)
        self._by_email[row.email] = row
        self._by_id[row.id] = row

    def find_by_email(self, email: str) -> UserRow | None:
        """按 email（大小写不敏感，模拟 citext）查用户，无则返回 None。"""
        return self._by_email.get(email.lower())

    def find_by_id(self, user_id: str) -> UserRow | None:
        """按 id 查用户，无则返回 None（供 JWT claims → 用户还原）。"""
        return self._by_id.get(user_id)


class InMemorySessionStore:
    """内存会话表：按 id 索引，保留插入顺序供列表查询。"""

    def __init__(self) -> None:
        """初始化空表与 id 索引。"""
        self._rows: dict[str, SessionRow] = {}

    def insert(self, row: SessionRow) -> None:
        """插入会话行（id 服务端生成，重复覆盖由上层保证不重复）。"""
        self._rows[row.id] = row

    def get(self, session_id: str) -> SessionRow | None:
        """按 id 查会话快照（返回副本，改后须显式 update；对齐 PG fetchone 语义）。"""
        row = self._rows.get(session_id)
        return replace(row) if row is not None else None

    def update(
        self, row: SessionRow, expected_state: SessionState | None = None
    ) -> None:
        """写回变更后的会话行（内存即覆盖；生产为 PG UPDATE）。"""
        # S2：乐观锁——前态不符说明并发已被他人改过（规则 10 不丢更新）
        if expected_state is not None:
            current = self._rows.get(row.id)
            if current is not None and current.state is not expected_state:
                raise ValueError("CONFLICT: state changed by another request")
        row.updated_at = datetime.now(UTC)
        self._rows[row.id] = row

    def list_by_user(self, user_id: str) -> list[SessionRow]:
        """按 user_id 过滤列出会话（租户隔离，§7）。"""
        return [r for r in self._rows.values() if r.user_id == user_id]


class AuditLedger:
    """只追加审计账本（内存实现；生产为 PG append-only 表）。"""

    def __init__(self) -> None:
        """初始化空账本与链头。"""
        self._rows: list[AuditEventRow] = []

    def append(
        self,
        user_id: str,
        session_id: str,
        action: str,
        decision: str,
        payload: AuditPayload,
        trace_id: str,
    ) -> AuditEventRow:
        """追加一条审计事件，计算 prev_hash/chain_hash 成链。

        Args:
            user_id: 事件归属用户（成功迁移为资源 owner，DENY 为尝试者）。
            session_id: 所属会话。
            action: 动作（AuditActions 常量：state_transition/interrupt/deny 等）。
            decision: 判定结果（OK / reason_code / FORBIDDEN）。
            payload: 证据载荷，禁含 PII 原文/附件原文/代码。
            trace_id: 链路追踪 id（供 M11 重放）。

        Returns:
            写入的 AuditEventRow（含计算出的 prev_hash/chain_hash）。

        S6：整行证据字段参与 hash（不只 payload），篡改任一字段都能被 chain_valid 检出。
        """
        prev = self._rows[-1].chain_hash if self._rows else ""
        # S6：整行证据字段入链（不只 payload），篡改 action/user_id/decision 也能被发现（A18）
        body = _canonical_body(user_id, session_id, action, decision, payload, trace_id)
        chain = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
        row = AuditEventRow(
            seq=len(self._rows),
            user_id=user_id,
            session_id=session_id,
            action=action,
            decision=decision,
            payload=payload,
            trace_id=trace_id,
            prev_hash=prev,
            chain_hash=chain,
            created_at=datetime.now(UTC),
        )
        self._rows.append(row)
        return row

    def chain_valid(self) -> bool:
        """重算整条 hash 链，任一环断裂返回 False（A18：可完整重放）。

        Returns:
            True 表示链完整（每行 prev_hash 等于前行 chain_hash 且全字段 hash 吻合）。
        """
        prev = ""
        for row in self._rows:
            body = _canonical_body(
                row.user_id,
                row.session_id,
                row.action,
                row.decision,
                row.payload,
                row.trace_id,
            )
            expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            if row.prev_hash != prev or row.chain_hash != expected:
                return False
            prev = row.chain_hash
        return True

    def by_session(self, session_id: str) -> list[AuditEventRow]:
        """按会话查审计事件（供 M11 重放与 A18 断言）。

        Args:
            session_id: 会话标识。
        """
        return [r for r in self._rows if r.session_id == session_id]

    def all(self) -> list[AuditEventRow]:
        """返回全部事件（副本）。"""
        return list(self._rows)


class SSEWindow:
    """最近 N 条事件的滑窗 + 按 seq 续传（I5：无重复无倒序）。"""

    def __init__(self, window_n: int) -> None:
        """注入窗口大小（SSE_WINDOW_N，默认 500，M02 Q4 定稿）。"""
        self._window_n = window_n
        self._events: list[SSEEvent] = []

    def emit(self, seq: int, kind: str, payload: JsonValue) -> None:
        """追加一条事件并裁剪超出窗口的旧事件（保留最近 N 条）。

        Args:
            seq: 会话内递增序号（由调用方保证连续、无重复）。
            kind: 事件类型（AgentEventKind 值）。
            payload: 事件载荷，JSON 兼容。

        超窗的旧事件由上层拉快照补（§4.5），这里直接丢弃不占内存。
        """
        self._events.append({"seq": seq, "kind": kind, "payload": payload})
        # 窗口裁剪：只保留最后 N 条，超窗的旧事件由上层拉快照补（§4.5）
        if len(self._events) > self._window_n:
            self._events = self._events[-self._window_n :]

    def since(
        self, last_event_id: int, session_id: str | None = None
    ) -> list[SSEEvent]:
        """返回 seq > last_event_id 的事件（A11 续传：从下一条按序补发）。

        内存窗口全局共享，session_id 参数仅用于对齐 EventWindow 协议。
        """
        return [e for e in self._events if e["seq"] > last_event_id]

    def all(self) -> list[SSEEvent]:
        """返回当前窗口内全部事件（副本，防外部修改内部状态）。

        Returns:
            按 seq 升序的事件列表拷贝。
        """
        return list(self._events)

    def last_seq(self) -> int:
        """返回窗口内最大 seq，空窗返回 -1（供快照 last_seq 锚点）。

        Returns:
            最大 seq；空窗 -1（与 §6.1 Session.last_seq 初始 0 区分：无事件即无锚点）。
        """
        return self._events[-1]["seq"] if self._events else -1


class SpySessionService(SessionService):
    """记录 AWAITING_INPUT 置位调用的会话服务（守护 B2：M05 不得直接改状态）。

    Attributes:
        awaiting_calls: 每次 mark_awaiting_input 的 session_id（按调用顺序）。
    """

    def __init__(self, store: SessionStore, audit: AuditLedgerPort) -> None:
        super().__init__(store, audit)
        self.awaiting_calls: list[str] = []

    def mark_awaiting_input(
        self,
        session_id: str,
        user_id: str,
        reason_code: str,
        evidence_ref: str | None = None,
    ) -> SessionRow:
        """记录一次置位请求后委托真实状态机。"""
        self.awaiting_calls.append(session_id)
        return super().mark_awaiting_input(
            session_id, user_id, reason_code, evidence_ref
        )


_TEST_JWT_SECRET = "test-only-secret-32-bytes-minimum"
"""测试专用 JWT 密钥（长度满足 HS256 推荐值，不用于任何生产环境）。"""


def build_test_app(
    *,
    user_store: UserStore | None = None,
    session_store: SessionStore | None = None,
    jwt_secret: str = _TEST_JWT_SECRET,
    event_source: EventWindow | None = None,
    attachment_service: AttachmentServicePort | None = None,
    audit: AuditLedgerPort | None = None,
    task_enqueuer: Callable[[Task], str] | None = None,
) -> FastAPI:
    """用测试替身装配 M04 app（生产 create_app 的镜像，默认依赖全为测试桩）。

    Args:
        user_store: 用户存储替身，默认 InMemoryUserStore。
        session_store: 会话存储替身，默认 InMemorySessionStore。
        jwt_secret: 测试密钥。
        event_source: SSE 事件源替身，默认 SSEWindow。
        attachment_service: M05 附件服务；None 时不挂载 M05 路由。
        audit: 审计账本替身，默认内存 AuditLedger。
        task_enqueuer: 入队桩；默认返回固定消息 id，不入真实 Redis。

    Returns:
        注入全部测试依赖的 FastAPI app。
    """
    from services.control_plane.api.server import create_app

    users = user_store or InMemoryUserStore()
    store = session_store or InMemorySessionStore()
    ledger = audit or AuditLedger()
    app = create_app(
        users,
        store,
        jwt_secret,
        event_source=event_source or SSEWindow(window_n=500),
        attachment_service=attachment_service,
        audit=ledger,
        task_enqueuer=task_enqueuer or (lambda task: "test-msg-id"),
    )
    # 与 app 共享同一存储/密钥的服务实例，供测试专用置位路由使用（见下函数）。
    app.state.test_sessions = SpySessionService(store, audit=ledger)
    app.state.test_user_store = users
    app.state.test_jwt_secret = jwt_secret
    return app


def install_test_awaiting_route(app: FastAPI) -> None:
    """把测试专用 AWAITING_INPUT 置位路由挂到测试 app（生产源码不注册）。

    Args:
        app: 由 build_test_app 构造的测试 app（读取 app.state 中的共享依赖）。
    """
    sessions: SessionService = app.state.test_sessions
    user_store: UserStore = app.state.test_user_store
    jwt_secret: str = app.state.test_jwt_secret
    auth = AuthService(user_store, jwt_secret)

    @app.post("/api/sessions/{session_id}/_test_awaiting")
    def test_awaiting(
        session_id: str, authorization: str | None = Header(default=None)
    ) -> ApiEnvelope:
        """测试钩子：模拟语义层判定转 AWAITING_INPUT。"""
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED")
        try:
            claims = auth.decode_access(authorization.split(" ", 1)[1])
        except AuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED") from exc
        user = user_store.find_by_id(claims.user_id)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED")
        try:
            row = sessions.mark_awaiting_input(
                session_id, user.id, ReasonCode.CLARIFICATION_REQUIRED.value
            )
        except PermissionError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "FORBIDDEN") from exc
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "CONFLICT") from exc
        return {"code": "OK", "data": {"state": row.state.value}}
