"""M09 bundles REST 路由（§4.1 六端点，挂到 control-plane create_app）。

纯服务端能力：前端只提供操作入口，user_id 从 JWT 取（不信任前端传参），
越权/不存在由 BundleService 折叠为 403/404。删除必须带 confirm_token。
返回统一信封 { code, message, traceId, retryable, evidenceRef, data }。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from packages.contracts.types import ApiEnvelope, JsonValue
from services.control_plane.accounts.stores import UserRow
from services.control_plane.bundles.errors import BundleError
from services.control_plane.bundles.ports import bundle_error_to_http
from services.control_plane.bundles.service import BundleService

# 二次确认 token 有效期（§6.3 推导 TTL 120s）。
_CONFIRM_TTL_SECONDS = 120


class DeleteReq(BaseModel):
    """删除请求体（§4.1 confirm_token 二次确认）。"""

    model_config = ConfigDict(extra="forbid")
    confirm_token: str = ""


class RollbackReq(BaseModel):
    """回滚请求体（§4.1 target_version_id 可选）。"""

    model_config = ConfigDict(extra="forbid")
    target_version_id: str | None = None


def _ok(data: JsonValue) -> ApiEnvelope:
    """成功响应信封（完整字段）。"""
    return {
        "code": "OK",
        "message": "OK",
        "traceId": uuid4().hex,
        "retryable": False,
        "data": data,
    }


def _handle(exc: BundleError) -> HTTPException:
    """领域异常 → HTTPException（§5.4 映射）。"""
    code_http, biz_code = bundle_error_to_http(exc)
    return HTTPException(code_http, {"code": biz_code, "message": exc.message})


def register_bundle_routes(
    app: FastAPI,
    service: BundleService,
    current_user: Callable[[str | None], UserRow],
) -> None:
    """把 bundles 端点挂到 app（current_user 为 create_app 内的 JWT 鉴权闭包）。

    Args:
        app: FastAPI 实例。
        service: 已注入真实依赖的 BundleService。
        current_user: JWT 鉴权依赖函数（Header 注入由 FastAPI 闭包处理）。
    """
    CurrentUser = Depends(current_user)

    @app.get("/api/bundles")
    def list_bundles(
        user: UserRow = CurrentUser,
        state: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ApiEnvelope:
        """列出本人产物（按 user_id 隔离）。"""
        items = service.list_bundles(user.id, state, limit)
        return _ok(cast(JsonValue, {"items": items, "next_cursor": ""}))

    @app.get("/api/bundles/{bundle_id}")
    def get_bundle(bundle_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """只读单包（越权 403）。"""
        try:
            return _ok(service.get(bundle_id, user.id))
        except BundleError as exc:
            raise _handle(exc) from exc

    @app.post("/api/bundles/{bundle_id}/install")
    def install_bundle(bundle_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """安装（幂等）。"""
        try:
            bundle = service.install(bundle_id, user.id)
            return _ok({"bundle_id": bundle_id, "state": bundle["state"]})
        except BundleError as exc:
            raise _handle(exc) from exc

    @app.post("/api/bundles/{bundle_id}/delete")
    def delete_bundle(
        bundle_id: str, req: DeleteReq, user: UserRow = CurrentUser
    ) -> ApiEnvelope:
        """删除（二次确认 + 不可逆）。"""
        try:
            bundle = service.delete(bundle_id, user.id, req.confirm_token)
            return _ok({"bundle_id": bundle_id, "state": bundle["state"]})
        except BundleError as exc:
            raise _handle(exc) from exc

    @app.get("/api/bundles/{bundle_id}/delete-confirm")
    def delete_confirm(bundle_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """取短期 confirm_token（§4.1 Q4）：服务端签发并绑定 user + bundle。"""
        try:
            token, expires_at = service.issue_delete_confirm(bundle_id, user.id)
        except BundleError as exc:
            raise _handle(exc) from exc
        return _ok({"confirm_token": token, "expires_at": expires_at})

    @app.post("/api/bundles/{bundle_id}/rollback")
    def rollback_bundle(
        bundle_id: str, req: RollbackReq, user: UserRow = CurrentUser
    ) -> ApiEnvelope:
        """回滚（沿用上一版编译产物）。"""
        try:
            bundle = service.rollback(bundle_id, user.id, req.target_version_id)
            versions = bundle.get("versions", [])
            current: JsonValue = {}
            if isinstance(versions, list) and versions:
                current = versions[-1]
            return _ok(
                {
                    "bundle_id": bundle_id,
                    "version_id": (
                        str(current.get("version_id", ""))
                        if isinstance(current, dict)
                        else ""
                    ),
                    "compiled_ref": (
                        str(current.get("compiled_ref", ""))
                        if isinstance(current, dict)
                        else ""
                    ),
                }
            )
        except BundleError as exc:
            raise _handle(exc) from exc

    @app.get("/api/bundles/{bundle_id}/preview")
    def preview_bundle(bundle_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """预览（srcdoc_ref + WidgetRuntime）。"""
        try:
            return _ok(service.preview(bundle_id, user.id))
        except BundleError as exc:
            raise _handle(exc) from exc

    @app.get("/api/bundles/{bundle_id}/evidence")
    def evidence_bundle(bundle_id: str, user: UserRow = CurrentUser) -> ApiEnvelope:
        """证据聚合（spec/manifest/校验/沙箱/trace）。"""
        try:
            return _ok(service.evidence(bundle_id, user.id))
        except BundleError as exc:
            raise _handle(exc) from exc
