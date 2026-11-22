"""M04 control-plane REST 路由（§4.1 端点表 / §4.4 统一响应）。

装配 AuthService + SessionService，Bearer JWT 鉴权依赖，统一响应 {code, data}；
租户隔离由 SessionService 内的 TenantGuard 执行（规则 10：API 真实消费服务，
无功能断点）。生产 app 用 create_app 注入 PG 适配器，测试注入内存存储。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from hashlib import sha256
from typing import (  # purity-exempt: FastAPI 路由返回 JSONResponse/信封双形态的框架边界
    TYPE_CHECKING,
    Any,  # purity-exempt: FastAPI 路由双形态返回值的框架边界（同文件内三处已登记）
    cast,
)
from uuid import uuid4

from fastapi import (
    Body,
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
from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.enums import ReasonCode, SessionState
from packages.contracts.types import ApiEnvelope, JsonObject, JsonValue
from services.control_plane.accounts.service import (
    AuthError,
    AuthService,
    DuplicateEmailError,
)
from services.control_plane.accounts.stores import UserRow, UserStore
from services.control_plane.api.guard import ForbiddenError
from services.control_plane.attachments.ports import AttachmentServicePort
from services.control_plane.audit.ledger import AuditLedgerPort
from services.control_plane.sessions.revision import (
    default_revision_phrases,
    detect_revocation_intent,
    merge_revised_description,
)
from services.control_plane.sessions.service import (
    REVISION_DECISION_MERGE,
    REVISION_DECISION_RERUN,
    ConflictError,
    SessionError,
    SessionService,
)
from services.control_plane.sessions.sse import EventWindow
from services.control_plane.sessions.stores import SessionRow, SessionStore
from services.guardrails.grid_resolver import resolve_grid
from services.orchestrator.memory.ports import PreferenceStorePort
from services.orchestrator.memory.types import PreferenceField, PreferenceSource
from services.orchestrator.tasks.fanout import needs_planning
from services.orchestrator.tasks.ports import TaskStorePort, TaskTurnStorePort
from services.orchestrator.tasks.states import TaskState, is_terminal

if TYPE_CHECKING:
    # task_enqueuer 的生产实现是 RedisTaskQueue.enqueue(task: Task) -> str；
    # 用精确 Task 别名与 str 返回值才能通过调用点的类型校验（无裸 object）。
    from services.orchestrator.dispatcher import Task

# 业务码常量（编码规则 5：语义字符串具名）
logger = logging.getLogger(__name__)
"""control-plane API 日志器（偏好学习等非门禁路径失败告警用）。"""

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
# B008 规避：DELETE preferences 的可选 body 单例化（编码规则）
_OPTIONAL_BODY = Body(default=None)


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
    target_task_id: str | None = None
    """ADR-009：指定修改哪个 widget；多任务时缺省触发 ASK_USER 消歧（O4 拍板）。"""


class ClarificationReq(BaseModel):
    """澄清回答请求体（v1.3 D-7，v1.8 A2 补 grid 回注）。"""

    model_config = ConfigDict(extra="forbid")
    question_id: str
    # R2-2（v1.8 复审）：空回答不得越过护栏（grid_confirmed 跳过充分性后，
    # 空串会连带绕过 R1）——用 min_length 强制非空。
    answer: str = Field(min_length=1)
    # A2（v1.8）：规格询问的回答若已定稿为 grid，前端一并回传，
    # 作为 clarified_grid 重入队，使 validate_chain 溯源 grid_source="clarified"。
    grid: str | None = None


class PatchRoleItem(BaseModel):
    """单条 role 纠偏项（S-3：必填字段由 Pydantic 校验，畸形请求 422 而非 500）。"""

    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    role: str


class PatchRolesReq(BaseModel):
    """附件 role 纠偏请求体（§4.1 PATCH attachment-roles）。"""

    model_config = ConfigDict(extra="forbid")
    roles: list[PatchRoleItem]


class PutPreferencesReq(BaseModel):
    """显式写入偏好请求体（ADR-002 D-10；只含白名单字段）。

    每字段可为标量或短字符串列表；Pydantic 拒绝未知字段（防学进 PII / 历史代码）。
    """

    model_config = ConfigDict(extra="forbid")
    palette: str | None = None
    """配色偏好（短字符串）。"""
    preferred_grids: list[str] | None = None
    """常用规格列表。"""
    preferred_widget_types: list[str] | None = None
    """常用控件类型列表。"""


class DeletePreferencesReq(BaseModel):
    """清除偏好请求体（ADR-002 D-13；fields 为空则全清）。"""

    model_config = ConfigDict(extra="forbid")
    fields: list[str] | None = None
    """待清除字段名；省略或空表示全字段清除。"""


def _m05_ok(data: JsonValue, evidence_ref: str = "") -> ApiEnvelope:
    """M05 成功响应（openapi ApiResponse 完整字段，S-1/N5）。"""
    return {
        "code": _CODE_OK,
        "message": "OK",
        "traceId": uuid4().hex,
        "retryable": False,
        "evidenceRef": evidence_ref,
        "data": data,
    }


def _m05_error(code: str, detail: str, evidence_ref: str = "") -> ApiEnvelope:
    """M05 失败响应（code 对齐 ReasonCode + data.detail.code + evidenceRef，S-1/A4/N5）。"""
    return {
        "code": code,
        "message": detail,
        "traceId": uuid4().hex,
        "retryable": False,
        "evidenceRef": evidence_ref,
        "data": {"detail": {"code": detail}},
    }


def _ok(data: JsonValue) -> ApiEnvelope:
    """成功响应包装（基线 §5.1 ApiResponse 的最小形态）。"""
    return {"code": _CODE_OK, "data": data}


def create_app(
    user_store: UserStore,
    session_store: SessionStore,
    jwt_secret: str,
    event_source: EventWindow,
    attachment_service: AttachmentServicePort | None,
    audit: AuditLedgerPort,
    task_enqueuer: Callable[[Task], str],
    preference_store: PreferenceStorePort | None = None,
    task_store: TaskStorePort | None = None,
    task_turn_store: TaskTurnStorePort | None = None,
) -> FastAPI:
    """构造 M04 API app（所有真实依赖必须显式注入，源码不保留测试桩）。

    Args:
        user_store: 用户存储（生产为 PgUserStore）。
        session_store: 会话存储（生产为 PgSessionStore）。
        jwt_secret: JWT 签名密钥（无默认值，缺配置即 fail-fast）。
        event_source: SSE 事件源（生产为 RedisEventSource）。
        attachment_service: M05 附件服务；None 时不挂载附件路由（部署形态，非兼容桩）。
        audit: 审计账本（生产为 PgAuditLedger）。
        task_enqueuer: 任务入队回调（生产为 RedisTaskQueue.enqueue）。
        preference_store: M07 偏好存储；None 时不挂载偏好路由（部署形态，非兼容桩）。
        task_store: ADR-006 generation_task 存储（生产 PgGenerationTaskStore）；
            None 时 tasks[] 摘要省略（单任务模式，非兼容桩）。
        task_turn_store: ADR-009 task_turn 存储（生产 PgTaskTurnStore）。
    """
    app = FastAPI(title="WidgetPilot control-plane M04", docs_url=None, redoc_url=None)
    auth = AuthService(user_store, jwt_secret)
    sessions = SessionService(session_store, audit=audit)
    events = event_source

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
    def register(req: RegisterReq) -> ApiEnvelope:
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
    def login(req: LoginReq) -> ApiEnvelope:
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
    def refresh(req: RefreshReq) -> ApiEnvelope:
        """S4：刷新 access token（M02 Q3 定稿路径；失效 refresh → 401）。"""
        try:
            access = auth.refresh(req.refresh_token)
        except AuthError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, _CODE_UNAUTHORIZED
            ) from exc
        return _ok({"access_token": access})

    @app.get("/api/sessions")
    def list_sessions(user: UserRow = CurrentUser) -> ApiEnvelope:
        """会话列表：按 user_id 隔离（§4.1）。"""
        return _ok({"items": [s.id for s in session_store.list_by_user(user.id)]})

    @app.post("/api/sessions", status_code=status.HTTP_200_OK)
    def create_session(
        req: CreateSessionReq, user: UserRow = CurrentUser
    ) -> ApiEnvelope:
        """新建会话：state=DRAFT（§5.2）。"""
        row = sessions.create(user.id, req.title, req.grid)
        return _ok(_session_view(row))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """会话快照：跨用户 403（A1）。"""
        try:
            row = sessions.snapshot(session_id, user.id)
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except SessionError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CODE_INPUT_INVALID) from exc
        view = _session_view(row)
        # ADR-006/009：会话内任务摘要（多任务视图恢复用；task_store 未注入时省略）。
        if task_store is not None:
            try:
                rows = task_store.list_by_session(session_id)
                view["tasks"] = [
                    {
                        "task_id": getattr(r, "id", ""),
                        "task_index": getattr(r, "task_index", 0),
                        "task_total": getattr(r, "task_total", 0),
                        "state": str(getattr(r, "state", "")),
                        "result_ref": getattr(r, "result_ref", None),
                        "current_bundle_ref": getattr(r, "current_bundle_ref", None),
                        "last_turn_index": getattr(r, "last_turn_index", 0),
                        "role": getattr(r, "role", None),
                        "turn": getattr(r, "turn", 0),
                    }
                    for r in rows
                ]
            except Exception as exc:  # noqa: BLE001 - 任务摘要是观测面，读失败省略不阻断会话快照
                logger.warning("task summary read failed: %s", exc)
        return _ok(view)

    @app.get("/api/tasks/{task_id}/turns")
    def list_task_turns(task_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """ADR-009 任务迭代版本历史（本人可见；task_turn_store 未注入时 404）。"""
        if task_turn_store is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CODE_INPUT_INVALID)
        turns = task_turn_store.list_by_task(task_id)
        if not turns:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CODE_INPUT_INVALID)
        return _ok(
            {
                "task_id": task_id,
                "turns": [
                    {
                        "turn_index": getattr(t, "turn_index", 0),
                        "run_id": getattr(t, "run_id", None),
                        "user_intent": getattr(t, "user_intent", ""),
                        "result_bundle_ref": getattr(t, "result_bundle_ref", None),
                        "fact_summary_ref": getattr(t, "fact_summary_ref", None),
                        "state": str(getattr(t, "state", "")),
                    }
                    for t in turns
                ],
            }
        )

    @app.post(
        "/api/sessions/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED
    )
    def post_message(
        session_id: str,
        req: MessageReq,
        user: UserRow = CurrentUser,
    ) -> ApiEnvelope:
        """提交描述：DRAFT→RUNNING，202 已受理（§5.5 异步语义）。

        ADR-010 §4：仅两类会话修正分支走新逻辑，其余请求保持原链路。
        """
        # ---- ADR-010 §4：识别两类修正场景（纯确定性判定，零模型） ----
        run_description = req.description
        revision_kind = ""
        revision_detail: dict[str, str | int | bool | None] | None = None
        try:
            current = sessions.get(session_id)
        except SessionError:
            current = None  # 不存在交回下方原链路处理（保持原 400 行为）
        if current is not None and current.state is SessionState.AWAITING_INPUT:
            # §4.1：用户撤回上一轮违法输入 → 合并 T1+T2，T1 原文不删。
            phrases = default_revision_phrases()
            if detect_revocation_intent(req.description, phrases.revocations):
                merged = merge_revised_description(
                    current.original_description,
                    req.description,
                    dangerous_phrases=phrases.dangerous_actions,
                    revocation_phrases=phrases.revocations,
                )
                run_description = merged.description
                revision_kind = REVISION_DECISION_MERGE
                # 合并证据写审计（只存哈希与剥离词，不写违法原文）。
                revision_detail = {
                    "stripped_phrases": ",".join(merged.stripped_phrases),
                    "merged_sha256": sha256(run_description.encode("utf-8")).hexdigest(),
                }
        elif (
            current is not None
            and current.state in (SessionState.RUNNING, SessionState.PAUSED)
            and req.description.strip() != current.original_description.strip()
        ):
            # §4.2：任务中途"改需求"→ 中断当前 run 后以新描述重走。
            # 描述未变仍是重复提交（原 400 语义），不误入重跑分支。
            run_description = req.description
            revision_kind = REVISION_DECISION_RERUN

        if revision_kind:
            # §4.2：interrupt 幂等（RUNNING→PAUSED，PAUSED 保持），checkpoint/审计不删。
            if revision_kind == REVISION_DECISION_RERUN:
                try:
                    sessions.interrupt(session_id, user.id)
                except ForbiddenError as exc:
                    raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
                except ConflictError as exc:
                    raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
                # 旧任务置 CANCELLED（可选注入 task_store，无则不强制）。
                if task_store is not None:
                    for task in task_store.list_by_session(session_id):
                        if not is_terminal(task.state):
                            task_store.update_state(task.id, TaskState.CANCELLED.value)
            try:
                row = sessions.submit_revision(
                    session_id,
                    user.id,
                    run_description,
                    keep_original=revision_kind == REVISION_DECISION_MERGE,
                    revision=revision_kind,
                    detail=revision_detail,
                )
            except ForbiddenError as exc:
                raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
            except (SessionError, ConflictError) as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, _CODE_INPUT_INVALID
                ) from exc
        else:
            # ---- 原有链路（零回归）：ADR-009 消歧 + submit_message ----
            # ADR-009 O4：多任务会话无 target_task_id → ASK_USER 消歧，不擅自选最近任务。
            if task_store is not None and not req.target_task_id:
                try:
                    existing = task_store.list_by_session(session_id)
                    if len(existing) > 1:
                        sessions.mark_awaiting_input(
                            session_id, user.id, "CLARIFICATION_REQUIRED", "task-disambiguation"
                        )
                        return _ok(
                            {
                                "state": "AWAITING_INPUT",
                                "reason_code": "CLARIFICATION_REQUIRED",
                                "question": "会话内有多个任务，请用 target_task_id 指定要修改的任务",
                                "tasks": [getattr(t, "id", "") for t in existing],
                            }
                        )
                except ForbiddenError as exc:
                    raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
                except Exception as exc:  # noqa: BLE001 - 消歧是增强路径，读失败沿用单任务链路
                    logger.warning("task disambiguation read failed: %s", exc)
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

        # B1b：迁移成功后入队，dispatcher 消费驱动 agent loop（生产注入真实队列）
        task_payload: Task = {
            "session_id": session_id,
            "user_id": user.id,  # review B1：挂起回写需真实 owner 做租户校验
            "run_id": row.run_id or "",
            "description": run_description,
            "grid": req.grid or "",
            # C1/A1（v1.8）：附件 id 随任务下传，dispatcher 过校验链时
            # 由 guardrail_adapter 解析成 AttachmentView（此前是死参数）。
            "attachment_ids": req.attachment_ids,
            # ADR-009：多任务消歧 / 迭代目标 task_id（单任务为空）。
            "target_task_id": req.target_task_id or "",
            # ADR-006：保守触发——仅多 widget 信号请求置 planning=1 走约束式规划器；
            # 单 widget 保持原 run() 直路零回归。触发只决定是否调规划器，N/描述/附件
            # 仍全由真实 PlanLLM 裁定（见 services/orchestrator/tasks/fanout.py）。
            "planning": "1" if needs_planning(run_description) else "0",
        }
        if revision_kind:
            # 下游据此识别修正轮次（仅两分支携带，正常路径不含该字段）。
            task_payload["revision"] = revision_kind
        task_enqueuer(task_payload)
        return _ok({"runId": row.run_id, "state": row.state.value})

    @app.post(
        "/api/sessions/{session_id}/clarifications",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def post_clarification(
        session_id: str,
        req: ClarificationReq,
        user: UserRow = CurrentUser,
    ) -> ApiEnvelope:
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
        # A2/B1（v1.8）：澄清后重入队，带澄清标记 + clarified_grid 走同一条校验链。
        # R2-1（复审）：answer 用 M06 resolve_grid 的别名规则解析（"四行四列"/"4X4"/
        # "就用4x4吧" 等），不再只做精确枚举匹配——否则合法纯文本回答仍死循环。
        # R2-2（复审）：澄清重入是独立语义（clarification_reply=1），grid_confirmed
        # 由该标记决定、不依赖 grid 是否解析成功（role 澄清等非规格回答也适用）；
        # 空 answer 已由 ClarificationReq.min_length 拒绝，防越过 R1。
        clarified = req.grid or ""
        if not clarified:
            try:
                resolved = resolve_grid(None, req.answer)
                if resolved.grid is not None:
                    clarified = resolved.grid.value
            except ValueError:
                clarified = ""
        task_enqueuer(
            {
                "session_id": session_id,
                "user_id": user.id,
                "run_id": row.run_id or "",
                # R3-1（v1.8 终审）：description 用首次提交的原始需求（已持久化到
                # SessionRow.original_description），answer 只进 clarification_answer
                # 槽位——否则澄清后模型只收到"四行四列"，丢失控件/特征需求。
                # 澄清态已跳过描述充分性，原描述过长不会误拒；注入扫描照常执行。
                "description": row.original_description or req.answer,
                "grid": clarified,
                "clarified_grid": clarified,
                "clarification_reply": "1",
            }
        )
        # M07 D-10 第 2 类信号：澄清点选的 grid 写偏好（clarification=0.8，零 LLM）。
        # 偏好是建议不是门禁：学习失败只丢证据，不影响澄清主流程。
        if preference_store is not None and clarified:
            from services.orchestrator.memory.preference_learning import (
                learn_from_clarification,
            )

            try:
                learn_from_clarification(
                    preference_store, user.id, session_id, clarified, uuid4().hex
                )
            except Exception:  # noqa: BLE001 - 偏好学习失败只丢证据，不阻断澄清
                logger.warning("clarification preference learning failed; continue")
        return _ok({"state": row.state.value})

    @app.post("/api/sessions/{session_id}/interrupt")
    def interrupt(session_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
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
    def resume(session_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """继续：终态 409（A13）；越权 403。"""
        try:
            row = sessions.resume(session_id, user.id)
        except ForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _CODE_FORBIDDEN) from exc
        except ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, _CODE_CONFLICT) from exc
        return _ok({"state": row.state.value, "runId": row.run_id})

    @app.post("/api/sessions/{session_id}/cancel")
    def cancel(session_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
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
        run_id: str | None = Query(default=None, alias="runId"),  # purity-exempt: SSE 查询参数契约名，不可改
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

        def frames() -> Iterator[str]:
            """生成 SSE 帧：id/event/data 三行 + 空行，末尾心跳注释帧。"""
            for event in events.since(last_event_id or 0, session_id):
                payload = json.dumps(event["payload"], default=str)
                yield f"id: {event['seq']}\nevent: {event['kind']}\ndata: {payload}\n\n"
            yield ": keepalive\n\n"

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # M05 附件路由（v1.7：description 随上传提交，角色判定在上传时完成）
    # 【已登记的框架边界豁免】返回标注用 Any：成功分支是契约 dict，错误分支直接返回
    # JSONResponse；FastAPI 会把 dict | JSONResponse 之类联合注解当 response_model 解析
    # 并拒绝注册，故此处不能用 ApiEnvelope 联合注解。
    if attachment_service is not None:

        @app.post("/api/attachments")
        async def upload_attachment(
            file: UploadFile = _FILE_ARG,
            session_id: str = _SESSION_FORM,
            description: str = _DESCRIPTION_FORM,
            user: UserRow = CurrentUser,
        ) -> Any:  # purity-exempt: FastAPI 返回 JSONResponse/信封双形态，response_model 拒绝联合注解
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
            if uploaded.processing_state == "FAILED":
                return JSONResponse(
                    status_code=500,
                    content=_m05_error(
                        "PROCESSOR_FAILED", "PROCESSOR_FAILED", uploaded.evidence_ref
                    ),
                )
            return _m05_ok(
                {
                    "attachment_id": uploaded.attachment_id,
                    "evidence_ref": uploaded.evidence_ref,
                    "kind": uploaded.kind,
                    "mime": uploaded.mime,
                    "size": uploaded.size,
                    "object_key": uploaded.object_key,
                    "hash": uploaded.hash,
                    "role": uploaded.role,
                    "role_confidence": uploaded.role_confidence,
                    "insight_ref": uploaded.insight_ref,
                    "insight_quality": uploaded.insight_quality,
                    # C2（v1.8）：role 歧义时透出澄清提示（与 notice 事件同值），
                    # 调用方无需订阅 SSE 即可立即拿到（M05 §1.4 v1.8 标注）。
                    "clarify_hint": uploaded.clarify_hint,
                }
            )

        @app.patch("/api/sessions/{session_id}/attachment-roles")
        def patch_attachment_roles(
            session_id: str, req: PatchRolesReq, user: UserRow = CurrentUser
        ) -> Any:  # purity-exempt: FastAPI 返回 JSONResponse/信封双形态，response_model 拒绝联合注解
            """role 纠偏 + 从该步重跑（§4.1 / S3 双入口态）。"""
            updated: list[JsonObject] = []
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
            return _m05_ok(
                cast(
                    JsonObject,
                    {"updated": updated, "rerun_from": "ROLE_ASSIGNED"},
                )
            )

        @app.get("/api/sessions/{session_id}/attachments")
        def list_attachments(
            session_id: str, user: UserRow = CurrentUser
        ) -> Any:  # purity-exempt: FastAPI 返回 JSONResponse/信封双形态，response_model 拒绝联合注解
            """列出会话附件（§4.1 / A12：越权 403，不泄漏是否存在）。"""
            try:
                rows = attachment_service.attachments.list_by_session(
                    user.id, session_id
                )
            except PermissionError:
                return JSONResponse(
                    status_code=403,
                    content=_m05_error(_CODE_FORBIDDEN, "FORBIDDEN"),
                )
            # 服务端归属校验：非本用户 session 一律 403（即使 store 返回空列表）
            from services.control_plane.attachments.tenant_guard import (
                SessionTenantGuard,
            )

            guard = SessionTenantGuard(session_store)
            try:
                guard.assert_owner(user.id, session_id)
            except PermissionError:
                return JSONResponse(
                    status_code=403,
                    content=_m05_error(_CODE_FORBIDDEN, "FORBIDDEN"),
                )
            return _m05_ok(
                {
                    "items": [
                        {
                            "attachment_id": r.id,
                            "role": r.role,
                            "role_confidence": r.role_confidence,
                            "insight_ref": r.insight_ref,
                            "insight_quality": r.insight_quality,
                            "clarify_hint": r.clarify_hint,
                            "processing_state": r.processing_state,
                        }
                        for r in rows
                    ]
                }
            )

    # ---------- M07 用户偏好（本人读写清除，ADR-002 D-9~D-14）----------
    # None 时不挂载（部署形态，非兼容桩）：偏好端点依赖真实 PG Store 注入。
    if preference_store is not None:

        @app.get("/api/users/me/preferences")
        def get_preferences(user: UserRow = CurrentUser) -> ApiEnvelope:
            """读取本人偏好（每字段带 source/weight/evidence_count/version）。"""
            pref = preference_store.get(user.id)
            return _ok(
                {
                    "user_id": pref.user_id,
                    "version": pref.version,
                    "fields": {
                        name.value: {
                            "value": ev.value,
                            "source": ev.source.value,
                            "weight": ev.weight,
                            "evidence_count": ev.evidence_count,
                            "updated_at": ev.updated_at,
                        }
                        for name, ev in pref.fields.items()
                    },
                }
            )

        @app.put("/api/users/me/preferences")
        def put_preferences(
            req: PutPreferencesReq, user: UserRow = CurrentUser
        ) -> ApiEnvelope:
            """显式写入偏好（explicit=1.0，四操作写入时裁定）。"""
            # 请求字段 → 白名单字段枚举：未知字段已被 Pydantic extra=forbid 拒绝。
            candidates: list[tuple[PreferenceField, JsonValue]] = []
            if req.palette is not None:
                candidates.append((PreferenceField.PALETTE, req.palette))
            if req.preferred_grids is not None:
                candidates.append(
                    (PreferenceField.PREFERRED_GRIDS, cast(JsonValue, req.preferred_grids))
                )
            if req.preferred_widget_types is not None:
                candidates.append(
                    (
                        PreferenceField.PREFERRED_WIDGET_TYPES,
                        cast(JsonValue, req.preferred_widget_types),
                    )
                )
            trace_id = uuid4().hex
            pref = preference_store.get(user.id)
            for field_name, value in candidates:
                pref, _event = preference_store.apply(
                    user.id, None, field_name, value,
                    PreferenceSource.EXPLICIT, trace_id,
                )
            return _ok({"user_id": pref.user_id, "version": pref.version})

        @app.delete("/api/users/me/preferences")
        def delete_preferences(
            req: DeletePreferencesReq | None = _OPTIONAL_BODY,
            user: UserRow = CurrentUser,
        ) -> ApiEnvelope:
            """清除偏好（失效不删行，事件保留 revoked_at；version+1）。"""
            raw_fields = req.fields if req and req.fields else None
            try:
                fields = (
                    [PreferenceField(f) for f in raw_fields] if raw_fields else None
                )
            except ValueError as exc:
                # ADR-002：偏好错误复用 INPUT_INVALID（不新增 ReasonCode）；
                # 非法字段名须 400 而非 500（避免泄漏内部实现）。
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, ReasonCode.INPUT_INVALID.value
                ) from exc
            trace_id = uuid4().hex
            pref = preference_store.clear(user.id, fields, trace_id)
            return _ok({"user_id": pref.user_id, "version": pref.version})

    # 测试钩子：把会话置为 AWAITING_INPUT（生产由 M06 语义层触发，此处仅供接线测试）
    return app


def _session_view(row: SessionRow) -> JsonObject:
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
        "bundle_ref": row.bundle_ref,
    }
