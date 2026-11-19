"""M04 control-plane REST 路由（§4.1 端点表 / §4.4 统一响应）。

装配 AuthService + SessionService，Bearer JWT 鉴权依赖，统一响应 {code, data}；
租户隔离由 SessionService 内的 TenantGuard 执行（规则 10：API 真实消费服务，
无功能断点）。生产 app 用 create_app 注入 PG 适配器，测试注入内存存储。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from packages.contracts.enums import SessionState
from services.control_plane.accounts.service import (
    AuthError,
    AuthService,
    DuplicateEmailError,
)
from services.control_plane.accounts.stores import UserRow, UserStore
from services.control_plane.api.guard import ForbiddenError
from services.control_plane.sessions.service import (
    ConflictError,
    SessionError,
    SessionService,
)
from services.control_plane.sessions.stores import SessionRow, SessionStore

# 业务码常量（编码规则 5：语义字符串具名）
_CODE_OK: str = "OK"
_CODE_INPUT_INVALID: str = "INPUT_INVALID"
_CODE_FORBIDDEN: str = "FORBIDDEN"
_CODE_UNAUTHORIZED: str = "UNAUTHORIZED"
_CODE_CONFLICT: str = "CONFLICT"
_AUTH_SCHEME: str = "Bearer"


class RegisterReq(BaseModel):
    """注册请求体（§5.2 / 基线 §5.2）。"""

    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class LoginReq(BaseModel):
    """登录请求体。"""

    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class CreateSessionReq(BaseModel):
    """新建会话请求体（v1.3：grid 可选）。"""

    model_config = ConfigDict(extra="forbid")
    title: str = ""
    grid: str | None = None


class MessageReq(BaseModel):
    """提交描述请求体（v1.3：grid 可选）。"""

    model_config = ConfigDict(extra="forbid")
    description: str
    grid: str | None = None
    attachment_ids: list[str] = []


class ClarificationReq(BaseModel):
    """澄清回答请求体（v1.3 D-7）。"""

    model_config = ConfigDict(extra="forbid")
    question_id: str
    answer: str


def _ok(data: object) -> dict[str, Any]:
    """成功响应包装（基线 §5.1 ApiResponse 的最小形态）。"""
    return {"code": _CODE_OK, "data": data}


def create_app(
    user_store: UserStore,
    session_store: SessionStore,
    jwt_secret: str,
) -> FastAPI:
    """构造 M04 API app（注入存储与密钥，便于测试替身与生产适配器切换）。"""
    app = FastAPI(title="WidgetPilot control-plane M04", docs_url=None, redoc_url=None)
    auth = AuthService(user_store, jwt_secret)
    sessions = SessionService(session_store)

    def current_user(authorization: str | None = Header(default=None)) -> UserRow:
        """Bearer JWT 鉴权依赖：解析 token → 还原用户，失败 401。"""
        if authorization is None or not authorization.startswith(_AUTH_SCHEME + " "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _CODE_UNAUTHORIZED)
        token = authorization.split(" ", 1)[1]
        try:
            claims = auth.decode_access(token)
        except AuthError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, _CODE_UNAUTHORIZED
            ) from exc
        user = user_store.find_by_id(claims.user_id)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _CODE_UNAUTHORIZED)
        return user

    # Depends 单例（B008 规避：不在每个路由的默认参数里重复调用函数；
    # current_user 是闭包，捕获本 app 的 auth/user_store，故在 create_app 内定义一次）
    CurrentUser = Depends(current_user)

    @app.post("/api/auth/register")
    def register(req: RegisterReq) -> dict[str, Any]:
        """注册：签发双令牌 + 用户信息；重复邮箱 409（§4.1）。"""
        try:
            result = auth.register(req.email, req.password)
        except DuplicateEmailError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
        return _ok(
            {
                "access_token": result.access_token,
                "refresh_token": result.refresh_token,
                "user": {
                    "id": result.user.id,
                    "email": result.user.email,
                    "role": result.user.role.value,
                    "share_enabled": result.user.share_enabled,
                },
            }
        )

    @app.post("/api/auth/login")
    def login(req: LoginReq) -> dict[str, Any]:
        """登录：校验凭据；失败 401（不泄漏账号是否存在）。"""
        try:
            result = auth.login(req.email, req.password)
        except AuthError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, _CODE_UNAUTHORIZED
            ) from exc
        return _ok(
            {
                "access_token": result.access_token,
                "refresh_token": result.refresh_token,
                "user": {"id": result.user.id, "role": result.user.role.value},
            }
        )

    @app.get("/api/sessions")
    def list_sessions(user: UserRow = CurrentUser) -> dict[str, Any]:
        """会话列表：按 user_id 隔离（§4.1）。"""
        return _ok({"items": [s.id for s in session_store.list_by_user(user.id)]})

    @app.post("/api/sessions", status_code=status.HTTP_200_OK)
    def create_session(
        req: CreateSessionReq, user: UserRow = CurrentUser
    ) -> dict[str, Any]:
        """新建会话：state=DRAFT（§5.2）。"""
        row = sessions.create(user.id, req.title, req.grid)
        return _ok(_session_view(row))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """会话快照：跨用户 403（A1）。"""
        try:
            row = sessions.snapshot(session_id, user.id)
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except SessionError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CODE_INPUT_INVALID) from exc
        return _ok(_session_view(row))

    @app.post(
        "/api/sessions/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED
    )
    def post_message(
        session_id: str,
        req: MessageReq,
        user: UserRow = CurrentUser,
    ) -> dict[str, Any]:
        """提交描述：DRAFT→RUNNING，202 已受理（§5.5 异步语义）。"""
        try:
            row = sessions.submit_message(
                session_id, req.description, req.attachment_ids
            )
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except (SessionError, ConflictError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, _CODE_INPUT_INVALID
            ) from exc
        return _ok({"runId": row.run_id, "state": row.state.value})

    @app.post(
        "/api/sessions/{session_id}/clarifications",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def post_clarification(
        session_id: str,
        req: ClarificationReq,
        user: UserRow = CurrentUser,
    ) -> dict[str, Any]:
        """回答澄清：AWAITING_INPUT→RUNNING（D-7）。"""
        try:
            row = sessions.answer_clarification(
                session_id, user.id, req.question_id, req.answer
            )
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except SessionError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, _CODE_INPUT_INVALID
            ) from exc
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
        return _ok({"state": row.state.value})

    @app.post("/api/sessions/{session_id}/interrupt")
    def interrupt(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """打断：幂等（A6）。"""
        row = sessions.interrupt(session_id)
        return _ok(
            {
                "state": row.state.value,
                "checkpoint_rev": row.checkpoint_rev,
                "last_seq": row.last_seq,
            }
        )

    @app.post("/api/sessions/{session_id}/resume")
    def resume(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """继续：终态 409（A13）。"""
        try:
            row = sessions.resume(session_id)
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
        return _ok({"state": row.state.value, "runId": row.run_id})

    @app.post("/api/sessions/{session_id}/cancel")
    def cancel(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """取消：任意非终态→CANCELLED。"""
        row = sessions.cancel(session_id)
        return _ok({"state": row.state.value})

    # 测试钩子：把会话置为 AWAITING_INPUT（生产由 M06 语义层触发，此处仅供接线测试）
    @app.post("/api/sessions/{session_id}/_test_awaiting")
    def test_awaiting(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """测试专用：模拟语义层判定转 AWAITING_INPUT（生产环境不注册此路由）。"""
        row = sessions.mark_awaiting_input(session_id, "CLARIFICATION_REQUIRED")
        return _ok({"state": row.state.value})

    return app


def _session_view(row: SessionRow) -> dict[str, object]:
    """Session 行 → 响应视图（确定键，避免暴露内部字段）。"""
    return {
        "id": row.id,
        "state": row.state.value
        if isinstance(row.state, SessionState)
        else str(row.state),
        "grid": row.grid,
        "last_seq": row.last_seq,
        "checkpoint_rev": row.checkpoint_rev,
        "reason_code": row.reason_code,
        "evidence_ref": row.evidence_ref,
    }
