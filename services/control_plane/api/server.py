"""M04 control-plane REST 路由（§4.1 端点表 / §4.4 统一响应）。

装配 AuthService + SessionService，Bearer JWT 鉴权依赖，统一响应 {code, data}；
租户隔离由 SessionService 内的 TenantGuard 执行（规则 10：API 真实消费服务，
无功能断点）。生产 app 用 create_app 注入 PG 适配器，测试注入内存存储。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
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
from services.control_plane.sessions.sse import EventWindow, SSEWindow
from services.control_plane.sessions.stores import SessionRow, SessionStore

# 业务码常量（编码规则 5：语义字符串具名）
_CODE_OK: str = "OK"
_CODE_INPUT_INVALID: str = "INPUT_INVALID"
_CODE_FORBIDDEN: str = "FORBIDDEN"
_CODE_UNAUTHORIZED: str = "UNAUTHORIZED"
_CODE_CONFLICT: str = "CONFLICT"
_AUTH_SCHEME: str = "Bearer"
# B008 规避：FastAPI 参数默认值单例化（编码规则，不在路由签名里重复调用函数）
_FILE_ARG = File(...)
_SESSION_FORM = Form(...)
_DESCRIPTION_FORM = Form(..., min_length=1)


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


class RefreshReq(BaseModel):
    """刷新令牌请求体（M02 Q3 定稿端点）。"""

    model_config = ConfigDict(extra="forbid")
    refresh_token: str


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


class PatchRoleItem(BaseModel):
    """单条 role 纠偏项（S-3：必填字段由 Pydantic 校验，畸形请求 422 而非 500）。"""

    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    role: str


class PatchRolesReq(BaseModel):
    """附件 role 纠偏请求体（§4.1 PATCH attachment-roles）。"""

    model_config = ConfigDict(extra="forbid")
    roles: list[PatchRoleItem]


def _m05_ok(data: object) -> dict[str, Any]:
    """M05 成功响应（openapi ApiResponse 完整四字段，S-1）。"""
    return {
        "code": _CODE_OK,
        "message": "OK",
        "traceId": uuid4().hex,
        "retryable": False,
        "data": data,
    }


def _m05_error(code: str, detail: str) -> dict[str, Any]:
    """M05 失败响应（code 对齐 ReasonCode + data.detail.code，S-1/A4）。"""
    return {
        "code": code,
        "message": detail,
        "traceId": uuid4().hex,
        "retryable": False,
        "data": {"detail": {"code": detail}},
    }


def _ok(data: object) -> dict[str, Any]:
    """成功响应包装（基线 §5.1 ApiResponse 的最小形态）。"""
    return {"code": _CODE_OK, "data": data}


def create_app(
    user_store: UserStore,
    session_store: SessionStore,
    jwt_secret: str,
    event_source: EventWindow | None = None,
    attachment_service: Any | None = None,
) -> FastAPI:
    """构造 M04 API app（注入存储与密钥，便于测试替身与生产适配器切换）。

    Args:
        event_source: SSE 事件源；None 时用空 SSEWindow（无事件流的部署可缺省）。
        attachment_service: M05 附件服务；None 时不挂载 M05 路由（向后兼容既有测试）。
    """
    app = FastAPI(title="WidgetPilot control-plane M04", docs_url=None, redoc_url=None)
    auth = AuthService(user_store, jwt_secret)
    sessions = SessionService(session_store)
    # 事件源缺省为空窗口（规则 9：协议必有默认实现）
    events = event_source or SSEWindow(window_n=500)

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

    @app.post("/api/auth/refresh")
    def refresh(req: RefreshReq) -> dict[str, Any]:
        """S4：刷新 access token（M02 Q3 定稿路径；失效 refresh → 401）。"""
        try:
            access = auth.refresh(req.refresh_token)
        except AuthError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, _CODE_UNAUTHORIZED
            ) from exc
        return _ok({"access_token": access})

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
                session_id, user.id, req.description, req.attachment_ids
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
        """打断：幂等（A6）；越权 403 / 状态冲突 409。"""
        try:
            row = sessions.interrupt(session_id, user.id)
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
        return _ok(
            {
                "state": row.state.value,
                "checkpoint_rev": row.checkpoint_rev,
                "last_seq": row.last_seq,
            }
        )

    @app.post("/api/sessions/{session_id}/resume")
    def resume(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """继续：终态 409（A13）；越权 403。"""
        try:
            row = sessions.resume(session_id, user.id)
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
        return _ok({"state": row.state.value, "runId": row.run_id})

    @app.post("/api/sessions/{session_id}/cancel")
    def cancel(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """取消：任意非终态→CANCELLED；越权 403。"""
        try:
            row = sessions.cancel(session_id, user.id)
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        return _ok({"state": row.state.value})

    @app.get("/api/sessions/{session_id}/events")
    def stream_events(
        session_id: str,
        user: UserRow = CurrentUser,
        run_id: str | None = Query(default=None, alias="runId"),
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """SSE 事件流（§4.1 L359）：先租户校验，按 Last-Event-ID 续传存量事件。

        有限流实现：发完当前窗口事件 + 一条心跳帧后关闭，客户端凭最后 seq
        重连续传（§4.5 超窗先拉快照）。生产长连接保活（异步订阅新事件）
        待与 M15 联调时启用；当前构件（SSEWindow/RedisEventBus）已就绪。
        """
        try:
            sessions.snapshot(session_id, user.id)  # 越权 403（每条流先校验归属）
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except SessionError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CODE_INPUT_INVALID) from exc

        def frames() -> object:
            """生成 SSE 帧：id/event/data 三行 + 空行，末尾心跳注释帧。"""
            for event in events.since(last_event_id or 0, session_id):
                payload = json.dumps(event["payload"], default=str)
                yield f"id: {event['seq']}\nevent: {event['kind']}\ndata: {payload}\n\n"
            yield ": keepalive\n\n"

        return StreamingResponse(
            frames(),  # type: ignore[arg-type]
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # M05 附件路由（v1.7：description 随上传提交，角色判定在上传时完成）
    if attachment_service is not None:

        @app.post("/api/attachments")
        async def upload_attachment(
            file: UploadFile = _FILE_ARG,
            session_id: str = _SESSION_FORM,
            description: str = _DESCRIPTION_FORM,
            user: UserRow = CurrentUser,
        ) -> dict[str, Any]:
            """附件上传：流式限量读取 + 传输层校验 + 存储 + role 判定。"""
            from services.control_plane.attachments.constants import (
                MAX_ATTACHMENT_BYTES,
            )
            from services.control_plane.attachments.validate import validate_transfer

            # B-2：分块读取至上限 +1 字节即停，防单请求 OOM（§3.1 流式接收）
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_ATTACHMENT_BYTES:
                    # 不再读取剩余 body，立即拒绝（413 + 契约错误响应）
                    return JSONResponse(
                        status_code=413,
                        content=_m05_error(_CODE_INPUT_INVALID, "ATTACHMENT_TOO_LARGE"),
                    )
            data = b"".join(chunks)

            content_type = file.content_type or "application/octet-stream"
            declared_kind = (
                "image"
                if content_type.startswith("image/")
                else "video"
                if content_type.startswith("video/")
                else "text"
            )
            result = validate_transfer(
                declared_kind,
                content_type,
                total,
                data[:16],
                len(
                    attachment_service.attachments.list_by_session(user.id, session_id)
                ),
            )
            if not result.ok:
                return JSONResponse(
                    status_code=413 if result.detail == "ATTACHMENT_TOO_LARGE" else 422,
                    content=_m05_error(
                        result.reason_code or _CODE_INPUT_INVALID, result.detail
                    ),
                )
            try:
                uploaded = attachment_service.upload(
                    user.id,
                    session_id,
                    data,
                    declared_kind,
                    content_type,
                    description,
                )
            except PermissionError:
                return JSONResponse(
                    status_code=403,
                    content=_m05_error(_CODE_FORBIDDEN, "FORBIDDEN"),
                )
            return _m05_ok(
                {
                    "attachment_id": uploaded.attachment_id,
                    "kind": uploaded.kind,
                    "mime": uploaded.mime,
                    "size": uploaded.size,
                    "object_key": uploaded.object_key,
                    "hash": uploaded.hash,
                    "role": uploaded.role,
                    "role_confidence": uploaded.role_confidence,
                    "insight_ref": uploaded.insight_ref,
                    "insight_quality": uploaded.insight_quality,
                }
            )

        @app.patch("/api/sessions/{session_id}/attachment-roles")
        def patch_attachment_roles(
            session_id: str, req: PatchRolesReq, user: UserRow = CurrentUser
        ) -> dict[str, Any]:
            """role 纠偏 + 从该步重跑（§4.1 / S3 双入口态）。"""
            updated: list[dict[str, str]] = []
            for item in req.roles:
                try:
                    result = attachment_service.patch_roles(
                        user.id, session_id, item.attachment_id, item.role
                    )
                except PermissionError:
                    return JSONResponse(
                        status_code=403,
                        content=_m05_error(_CODE_FORBIDDEN, "FORBIDDEN"),
                    )
                except ValueError as exc:
                    return JSONResponse(
                        status_code=422,
                        content=_m05_error(_CODE_INPUT_INVALID, str(exc)),
                    )
                updated.append(
                    {
                        "attachment_id": result.attachment_id,
                        "role": result.role or "",
                        "insight_ref": result.insight_ref or "",
                    }
                )
            return _m05_ok({"updated": updated, "rerun_from": "ROLE_ASSIGNED"})

    # 测试钩子：把会话置为 AWAITING_INPUT（生产由 M06 语义层触发，此处仅供接线测试）
    @app.post("/api/sessions/{session_id}/_test_awaiting")
    def test_awaiting(session_id: str, user: UserRow = CurrentUser) -> dict[str, Any]:
        """测试专用：模拟语义层判定转 AWAITING_INPUT（生产环境不注册此路由）。"""
        try:
            row = sessions.mark_awaiting_input(
                session_id, user.id, "CLARIFICATION_REQUIRED"
            )
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
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
