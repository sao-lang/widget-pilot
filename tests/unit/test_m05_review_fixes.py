"""S5 独立 review 回归测试（B-1/B-2/S-2/S-3/S-4/S-5/S-6/N-3/N-4）。

每条对应 review 报告的一条 finding；先 RED 再 GREEN。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from packages.contracts.enums import SessionState
from services.control_plane.accounts.service import AuthService
from services.control_plane.attachments.constants import MAX_ATTACHMENT_BYTES
from services.control_plane.attachments.service import AttachmentService
from services.control_plane.attachments.validate import validate_transfer
from services.control_plane.sessions.stores import SessionRow
from tests.support import (
    DispatchProcessor,
    FailingProcessor,
    FakeTenantGuard,
    InMemoryAttachmentStore,
    InMemoryEventSink,
    InMemoryObjectStore,
    InMemorySessionStore,
    InMemoryUserStore,
    build_test_app,
)

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8
_TEXT = b"date,value\n2026-01-01,42\n"


def _svc(
    owner: str = "u1", processor: DispatchProcessor | FailingProcessor | None = None
) -> AttachmentService:
    return AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({owner: ["s1"]}),
        processor=processor or DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )


# ---- B-1：合法文本 mime 不得被当成伪造 ----
@pytest.mark.parametrize("mime", ["text/plain", "text/csv", "application/json"])
def test_b1_legal_text_mime_accepted(mime: str) -> None:
    """B-1：合法文本 mime 嗅探为 text/plain 时应视为一致，不得拒绝 CSV/JSON。"""
    result = validate_transfer("text", mime, len(_TEXT), _TEXT, 0)
    assert result.ok is True, result.detail


# ---- S-4：声明 mime 白名单，octet-stream 不得作为声明值通过 ----
def test_s4_octet_stream_declaration_rejected() -> None:
    """S-4：二进制声明 application/octet-stream 不得绕过类型门禁成为 text。"""
    exe = b"MZ" + b"\x00" * 14
    result = validate_transfer("text", "application/octet-stream", len(exe), exe, 0)
    assert result.ok is False


# ---- N-3：负数 size 拒绝 ----
def test_negative_size_rejected() -> None:
    """N-3：size 为负应被拒（防止绕过上限校验）。"""
    result = validate_transfer("image", "image/png", -1, _PNG, 0)
    assert result.ok is False


# ---- S-2：PATCH 幂等（同 role 不重复重跑）----
def test_s2_patch_same_role_is_noop() -> None:
    """S-2：role 相同且已 INSIGHT_READY 时 PATCH 不重跑，insight_ref 不变。"""
    svc = _svc()
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    patched = svc.patch_roles("u1", "s1", r.attachment_id, "visual_reference")
    assert patched.insight_ref == r.insight_ref
    assert not svc.objects.versions(r.attachment_id)


# ---- S-6：服务走 processors.dispatch，handler 有真实调用记录 ----
def test_s6_service_dispatch_logged() -> None:
    """S-6：服务上传应记录真实处理器调用（不是只读字典字符串）。"""
    processor = DispatchProcessor()
    svc = _svc(processor=processor)
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert r.handler == "VisualRefProcessor"
    assert processor.handlers == ["VisualRefProcessor"]


# ---- S-6：处理器失败 → FAILED + evidenceRef ----
def test_s6_processor_failure_marks_failed() -> None:
    """A17/S-6：处理器超时 → processing_state=FAILED + evidenceRef 非空。"""
    svc = _svc(processor=FailingProcessor())
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert r.processing_state == "FAILED"
    assert r.evidence_ref


# ---- S-5：A10 扫描必须覆盖 insight 全文（含 extracted 内引用）----
def test_s5_original_not_in_serialized_extracted() -> None:
    """S-5：extracted（下游面向 M07/公共语料的部分）序列化后不得含 original 路径。

    source_ref 按设计 §6.2 合法指向原文用于溯源，不在红线范围内；
    红线针对的是会随产物/语料外流的 extracted 派生引用。
    """
    svc = _svc()
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    extracted_blob = svc.serialize_extracted()
    assert "/original" not in extracted_blob
    assert r.object_key not in extracted_blob


def _api_client(svc: AttachmentService) -> tuple[TestClient, dict[str, str]]:
    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    auth = AuthService(users, "a-very-long-test-secret-key-for-hs256-32bytes")
    reg = auth.register("review@m05.test", "password123")
    user = users.find_by_email("review@m05.test")
    assert user is not None
    sessions.insert(SessionRow(id="s1", user_id=user.id, state=SessionState.DRAFT))
    svc.tenant = FakeTenantGuard({user.id: ["s1"]})
    app = build_test_app(
        user_store=users,
        session_store=sessions,
        jwt_secret="a-very-long-test-secret-key-for-hs256-32bytes",
        attachment_service=svc,
    )
    return TestClient(app), {"Authorization": f"Bearer {reg.access_token}"}


# ---- S-1：API 响应符合 ApiResponse 契约 ----
def test_s1_api_ok_has_contract_fields() -> None:
    """S-1：成功响应须含 code/message/traceId/retryable。"""
    client, headers = _api_client(_svc())
    resp = client.post(
        "/api/attachments",
        headers=headers,
        data={"session_id": "s1", "description": "把这张图当背景"},
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    for key in ("code", "message", "traceId", "retryable"):
        assert key in body, key


def test_s1_api_error_has_contract_fields() -> None:
    """S-1/A4：413 错误响应须含 code=INPUT_INVALID + detail.code。"""
    client, headers = _api_client(_svc())
    big = b"\x00" * (MAX_ATTACHMENT_BYTES + 1)
    resp = client.post(
        "/api/attachments",
        headers=headers,
        data={"session_id": "s1", "description": "当背景"},
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "INPUT_INVALID"
    assert body["data"]["detail"]["code"] == "ATTACHMENT_TOO_LARGE"


# ---- S-3：畸形 PATCH 请求体返回 422 而非 500 ----
def test_s3_malformed_patch_returns_422() -> None:
    """S-3：缺 attachment_id 的 PATCH 应 422，不得 500。"""
    client, headers = _api_client(_svc())
    resp = client.patch(
        "/api/sessions/s1/attachment-roles",
        headers=headers,
        json={"roles": [{"role": "visual_reference"}]},
    )
    assert resp.status_code == 422


# ---- N-4：空 description 返回 422 ----
def test_n4_empty_description_returns_422() -> None:
    """N-4：description 空串应 422（openapi minLength:1）。"""
    client, headers = _api_client(_svc())
    resp = client.post(
        "/api/attachments",
        headers=headers,
        data={"session_id": "s1", "description": ""},
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert resp.status_code == 422


# ---- R-1：FAILED 行不得被幂等键复用（重试应能重新处理）----
def test_r1_failed_row_not_reused_by_idempotency() -> None:
    """R-1：处理器失败后同输入重试不得复用 FAILED 行，应重新走成功路径。"""
    svc = _svc(processor=FailingProcessor())
    first = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert first.processing_state == "FAILED"
    second = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert second.processing_state == "INSIGHT_READY"
    assert second.insight_ref
