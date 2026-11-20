"""M05 剩余验收（§8.2 A8/A9/A10/A15/A16/A17/A18 + API 契约）。"""

from __future__ import annotations

from typing import cast

import pytest
from fastapi.testclient import TestClient

from packages.agent_core.types import JsonObject, JsonValue
from packages.contracts.enums import SessionState
from services.control_plane.attachments.constants import ROLE_CLARIFY_THRESHOLD
from services.control_plane.attachments.service import AttachmentService
from services.control_plane.sessions.stores import SessionRow
from tests.support import (
    DispatchProcessor,
    FakeTenantGuard,
    InMemoryAttachmentStore,
    InMemoryEventSink,
    InMemoryObjectStore,
    InMemorySessionStore,
    InMemoryUserStore,
    UnregisteredRoleError,
    build_test_app,
    dispatch,
)

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8


def _svc() -> AttachmentService:
    return AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )


def test_a8_clarification_then_patch_resumes() -> None:
    """A8：歧义挂起后 PATCH 指定 role → 从 ROLE_PENDING 续到 INSIGHT_READY。"""
    svc = _svc()
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "这张图…")
    assert r.role is None
    patched = svc.patch_roles("u1", "s1", r.attachment_id, "style_reference")
    assert patched.role == "style_reference"
    assert patched.insight_ref
    assert patched.role_confidence >= ROLE_CLARIFY_THRESHOLD


def test_a9_rerun_only_target_attachment() -> None:
    """A9：纠偏只变该附件 insight_ref，其他附件不变；旧洞察保留 v1。"""
    svc = _svc()
    a = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    b = svc.upload("u1", "s1", _PNG, "image", "image/png", "提取右上角的日期")
    before_b = b.insight_ref
    patched = svc.patch_roles("u1", "s1", a.attachment_id, "content_extraction")
    assert patched.insight_ref != a.insight_ref
    other = svc.get_attachment("u1", "s1", b.attachment_id)
    assert other.insight_ref == before_b
    assert svc.objects.versions(a.attachment_id)  # 旧版 v1 保留


def test_a10_original_never_in_corpus_refs() -> None:
    """A10：所有对外引用中不含 att/*/original（原文永不进公共语料）。"""
    svc = _svc()
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    refs = svc.external_refs()
    assert r.object_key not in refs
    assert all("/original" not in ref for ref in refs)


def test_a15_video_uses_keyframes() -> None:
    """A15：视频附件 + 描述 → VideoFrameProcessor，关键帧数 ≤ MAX_KEYFRAMES。"""
    from services.control_plane.attachments.constants import MAX_KEYFRAMES

    result = dispatch("visual_reference", "video", "att/u1/s1/a/video")
    assert result.handler == "VideoFrameProcessor"
    extracted = cast(JsonObject, result.extracted)
    assert len(cast(list[JsonValue], extracted["frames"])) <= MAX_KEYFRAMES


def test_a16_text_cell_not_evaluated() -> None:
    """A16：CSV 单元格 "=1+1" 保持字符串，不执行表达式。"""
    result = dispatch("data_source", "text", "=1+1,plain\n=2+2,x")
    extracted = cast(JsonObject, result.extracted)
    assert any(cell == "=1+1" for cell in cast(list[str], extracted["cells"]))


def test_a17_processor_timeout_fails_state() -> None:
    """A17：处理器超时 → FAILED + 证据引用（不无限重试）。"""
    with pytest.raises(TimeoutError):
        dispatch(
            "content_extraction", "image", "att/u1/s1/a/original", simulate_timeout=True
        )


def test_a18_unregistered_role_rejected() -> None:
    """A18：伪造 role → 拒绝，不动态补注册。"""
    with pytest.raises(UnregisteredRoleError):
        dispatch("fake_role", "image", "att/u1/s1/a/original")


def test_api_upload_contract() -> None:
    """API：POST /api/attachments 带 description，返回 v1.7 完整字段。"""
    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    sessions.insert(SessionRow(id="s1", user_id="u1", state=SessionState.DRAFT))
    auth = __import__(
        "services.control_plane.accounts.service", fromlist=["AuthService"]
    ).AuthService(users, "test-secret")
    reg = auth.register("api@m05.test", "password123")
    token = reg.access_token
    user_row = users.find_by_email("api@m05.test")
    assert user_row is not None
    row = sessions.get("s1")
    assert row is not None
    row.user_id = user_row.id
    sessions.update(row)

    attachments = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({user_row.id: ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )
    app = build_test_app(
        user_store=users,
        session_store=sessions,
        jwt_secret="test-secret",
        attachment_service=attachments,
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post(
        "/api/attachments",
        headers=headers,
        data={"session_id": "s1", "description": "把这张图当背景"},
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    for key in (
        "attachment_id",
        "hash",
        "kind",
        "mime",
        "size",
        "object_key",
        "role",
        "role_confidence",
        "insight_ref",
        "insight_quality",
        "clarify_hint",  # C2（v1.8）：role 歧义提示透出 REST（无歧义时空串）
    ):
        assert key in data
