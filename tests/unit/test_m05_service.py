"""M05 role 判定 / 对象存储 / 服务编排验收（§8.2 A1/A2/A7/A11/A13/A14）。"""

from __future__ import annotations

from typing import cast

import pytest

from services.control_plane.attachments.constants import ROLE_CLARIFY_THRESHOLD
from services.control_plane.attachments.role_resolver import resolve_role
from services.control_plane.attachments.service import AttachmentService
from tests.support import (
    DispatchProcessor,
    FakeTenantGuard,
    InMemoryAttachmentStore,
    InMemoryEventSink,
    InMemoryObjectStore,
)

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8
_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8


def test_a1_description_drives_visual_role() -> None:
    """A1：描述「当背景」→ visual_reference。"""
    decision = resolve_role("把这张图当背景", "image")
    assert decision.role == "visual_reference"
    assert decision.role_confidence >= ROLE_CLARIFY_THRESHOLD


def test_a2_same_attachment_two_descriptions_two_paths() -> None:
    """A2：同一附件两种描述 → 两个不同 role + 不同 handler + 不同 insight_ref。"""
    a = resolve_role("把这张图当背景", "image")
    b = resolve_role("提取图片右上角的日期", "image")
    assert a.role == "visual_reference"
    assert b.role == "content_extraction"
    assert a.role != b.role

    svc = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )
    r1 = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    r2 = svc.upload("u1", "s1", _PNG, "image", "image/png", "提取图片右上角的日期")
    assert r1.role == "visual_reference"
    assert r2.role == "content_extraction"
    assert r1.handler != r2.handler
    assert r1.insight_ref != r2.insight_ref


def test_a7_ambiguous_role_no_default() -> None:
    """A7：描述无线索 → role=None + confidence 低 + clarify_hint，不默认任何 role。"""
    decision = resolve_role("这张图…", "image")
    assert decision.role is None
    assert decision.role_confidence < ROLE_CLARIFY_THRESHOLD

    sink = InMemoryEventSink()
    svc = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=sink,
    )
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "这张图…")
    assert r.role is None
    assert r.insight_ref is None
    assert r.clarify_hint
    notices = [e for e in sink.events if e["kind"] == "notice"]
    assert notices
    payload = notices[-1]["payload"]
    assert isinstance(payload, dict)
    assert payload["attachment_id"] == r.attachment_id
    # B2 定稿：澄清只经事件通道；「无 AWAITING_INPUT」由上面 notice 事件断言覆盖，
    # AttachmentService 已不持有会话状态机（不再有可断言的 state_sink）。


def test_a11_hash_verify_and_tamper() -> None:
    """A11：写入 original 后按 hash 读取成功，篡改 1 字节抛错。"""
    store = InMemoryObjectStore()
    ref = store.put_original("u1", "s1", "a1", _PNG)
    assert store.get_with_hash_verify(ref.key, ref.hash) == _PNG
    store.tamper(ref.key)
    with pytest.raises(ValueError, match="hash mismatch"):
        store.get_with_hash_verify(ref.key, ref.hash)


def test_a13_upload_idempotent() -> None:
    """A13：同一文件 + 同一幂等键两次上传 → 同一 attachment_id，对象仅 1 份。"""
    svc = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )
    r1 = svc.upload("u1", "s1", _PNG, "image", "image/png", "当背景")
    r2 = svc.upload("u1", "s1", _PNG, "image", "image/png", "当背景")
    assert r1.attachment_id == r2.attachment_id
    originals = [
        k for k in cast(InMemoryObjectStore, svc.objects).objects if k.endswith("/original")
    ]
    assert len(originals) == 1


def test_a14_insight_structure() -> None:
    """A14：AttachmentInsight 结构合规，source_ref 带 hash，insight_quality∈[0,1]。"""
    svc = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert r.insight_ref is not None
    insight = svc.read_insight(r.insight_ref)
    assert "#sha256:" in insight.source_ref
    assert 0.0 <= insight.insight_quality <= 1.0
    assert insight.extracted is not None


def test_video_no_role_hint_still_produces_insight() -> None:
    """v1.9：视频描述无线索时不误入歧义分支——处理器不依赖 role，照常产出洞察。"""
    sink = InMemoryEventSink()
    svc = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=sink,
    )
    # "看看这个" 无 role 关键词 → decision.role=None，但视频不该被卡在澄清态
    r = svc.upload("u1", "s1", _MP4, "video", "video/mp4", "看看这个")
    assert r.processing_state == "INSIGHT_READY"
    assert r.insight_ref is not None
    assert not r.clarify_hint
    assert not [e for e in sink.events if e["kind"] == "notice"]


def test_clarify_hint_varies_by_kind() -> None:
    """v1.9：歧义提示按附件类别给不同文案，且与 notice 事件同值。"""
    for kind, data, mime, word in (
        ("image", _PNG, "image/png", "图"),
        ("text", b"plain,text,rows", "text/csv", "文件"),
    ):
        sink = InMemoryEventSink()
        svc = AttachmentService(
            objects=InMemoryObjectStore(),
            attachments=InMemoryAttachmentStore(),
            tenant=FakeTenantGuard({"u1": ["s1"]}),
            processor=DispatchProcessor(),
            event_sink=sink,
        )
        r = svc.upload("u1", "s1", data, kind, mime, "看看这个")
        assert r.processing_state == "CLARIFICATION_NEEDED"
        assert word in r.clarify_hint
        notices = [e for e in sink.events if e["kind"] == "notice"]
        assert notices
        payload = notices[-1]["payload"]
        assert isinstance(payload, dict)
        assert payload["clarify_hint"] == r.clarify_hint


def test_tenant_guard_denies_cross_user() -> None:
    """A12：越权（user_id 不拥有 session_id）→ 拒绝，不读文件内容。"""
    svc = AttachmentService(
        objects=InMemoryObjectStore(),
        attachments=InMemoryAttachmentStore(),
        tenant=FakeTenantGuard({"u1": ["s1"]}),
        processor=DispatchProcessor(),
        event_sink=InMemoryEventSink(),
    )
    with pytest.raises(PermissionError, match="FORBIDDEN"):
        svc.upload("u2", "s1", _PNG, "image", "image/png", "当背景")
