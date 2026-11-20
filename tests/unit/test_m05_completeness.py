"""M05 功能完整性审计回归测试（A15/A17 + N2/N3/N11/N14）。

每条对应独立审计的一条 finding；先 RED 后 GREEN。
"""

from __future__ import annotations

import subprocess

import pytest

from services.control_plane.attachments.service import AttachmentService
from services.control_plane.attachments.stores import (
    FakeTenantGuard,
    InMemoryAttachmentStore,
    InMemoryObjectStore,
)

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8


class _SpyProcessor:
    """记录 process 调用（N2：验证纠偏偏走真实注入处理器）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def process(self, data: bytes, role: str, kind: str, source_ref: str):  # type: ignore[no-untyped-def]
        from services.control_plane.attachments.real_processors import ProcessorResult

        self.calls.append((role, kind))
        return ProcessorResult("SpyProcessor", {"spy": True}, 1.0)


def test_n2_patch_roles_uses_injected_processor() -> None:
    """N2 [Blocker]：纠偏重跑必须走注入的 processor，而非静态桩 dispatch。"""
    spy = _SpyProcessor()
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
        processor=spy,
    )
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    svc.patch_roles("u1", "s1", r.attachment_id, "style_reference")
    # 至少两次调用（upload + patch），且 patch 用新 role
    assert ("style_reference", "image") in spy.calls


def test_a17_timeout_expired_caught() -> None:
    """A17 [Blocker]：subprocess.TimeoutExpired 必须被捕获 → FAILED + evidenceRef。"""
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
    )

    # 直接让 processor 抛真实 ffmpeg 超时异常类型
    class Boom:
        def process(self, *a: object) -> object:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=20)

    svc.processor = Boom()
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert r.processing_state == "FAILED"
    assert r.evidence_ref


def test_n11_evidence_ref_is_written_object() -> None:
    """N11：失败 evidence_ref 必须指向真实写入的对象（非悬空 key）。"""
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
    )
    svc.fail_next_dispatch = True
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    key = r.evidence_ref.partition("#sha256:")[0].partition("?")[0]
    assert svc.objects.get_raw(key) is not None


def test_a15_video_extracted_has_regions() -> None:
    """A15 [Blocker]：视频 extracted 必须含 OCR regions（提取画面里的日期）。"""
    import subprocess
    import tempfile
    from pathlib import Path

    import imageio_ffmpeg
    from PIL import Image, ImageDraw

    from services.control_plane.attachments.real_processors import process_video

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(3):
            img = Image.new("RGB", (200, 80), "white")
            ImageDraw.Draw(img).text((10, 30), "2026-11-20", fill="black")
            img.save(Path(tmp) / f"f{i:02d}.png")
        enc = subprocess.run(
            [
                exe,
                "-y",
                "-framerate",
                "1",
                "-i",
                str(Path(tmp) / "f%02d.png"),
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                str(Path(tmp) / "out.mp4"),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert enc.returncode == 0
        result = process_video((Path(tmp) / "out.mp4").read_bytes())

    assert result.extracted["frame_count"] >= 1
    assert "regions" in result.extracted
    assert isinstance(result.extracted["regions"], list)


def test_n3_s3_versions_filters_by_attachment() -> None:
    """N3：S3ObjectStore.versions(attachment_id) 不能跨附件返回版本。"""
    from services.control_plane.attachments.s3_store import S3ObjectStore

    # monkeypatch _list 避免真实 S3，验证过滤逻辑
    store = S3ObjectStore()
    store._list = lambda prefix: [  # type: ignore[method-assign]
        "att/u1/s1/aaa/insight.v1.json",
        "att/u1/s1/bbb/insight.v2.json",
    ]
    result = store.versions("aaa")
    assert all("/aaa/" in k for k in result)


# ---- A8/A7：attachment + notice 事件发射（seq 单调）----
def test_a8_upload_emits_attachment_event() -> None:
    """A8：上传成功必须发 attachment 事件，seq 单调 > 0。"""
    from services.control_plane.attachments.events import InMemoryEventSink

    sink = InMemoryEventSink()
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
        event_sink=sink,
    )
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    evs = [e for e in sink.events if e["kind"] == "attachment"]
    assert evs and evs[0]["session_id"] == "s1"
    assert evs[0]["seq"] > 0
    assert evs[0]["payload"]["attachment_id"] == r.attachment_id


def test_a7_ambiguous_emits_notice() -> None:
    """A7：歧义必须发 notice 事件（clarify_hint 可消费），不置 AWAITING_INPUT。"""
    from services.control_plane.attachments.events import InMemoryEventSink

    sink = InMemoryEventSink()
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
        event_sink=sink,
    )
    svc.upload("u1", "s1", _PNG, "image", "image/png", "这张图…")
    notices = [e for e in sink.events if e["kind"] == "notice"]
    assert notices and notices[0]["payload"]["clarify_hint"]
    assert svc.state_sink.awaiting_calls == []


def test_a8_patch_emits_new_seq() -> None:
    """A8：PATCH 后事件 seq 必须大于首次上传。"""
    from services.control_plane.attachments.events import InMemoryEventSink

    sink = InMemoryEventSink()
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
        event_sink=sink,
    )
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    first = max(e["seq"] for e in sink.events)
    svc.patch_roles("u1", "s1", r.attachment_id, "style_reference")
    later = [e for e in sink.events if e["kind"] == "attachment" and e["seq"] > first]
    assert later


# ---- A12：GET 附件端点 + 审计 deny ----
def test_a12_get_attachments_cross_user_403() -> None:
    """A12 [Blocker]：GET /api/sessions/{id}/attachments 越权 403。"""

    from fastapi.testclient import TestClient

    from services.control_plane.accounts.service import AuthService
    from services.control_plane.accounts.stores import InMemoryUserStore
    from services.control_plane.api.server import create_app
    from services.control_plane.sessions.stores import InMemorySessionStore, SessionRow

    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    auth = AuthService(users, "a-very-long-test-secret-key-for-hs256-32bytes")
    reg = auth.register("cross@m05.test", "password123")
    user = users.find_by_email("cross@m05.test")
    assert user is not None
    sessions.insert(SessionRow(id="other-s1", user_id="some-other-user", state="DRAFT"))
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({user.id: ["own-s1"]}),
    )
    app = create_app(
        users,
        sessions,
        "a-very-long-test-secret-key-for-hs256-32bytes",
        attachment_service=svc,
    )
    client = TestClient(app)
    resp = client.get(
        "/api/sessions/other-s1/attachments",
        headers={"Authorization": f"Bearer {reg.access_token}"},
    )
    assert resp.status_code == 403


# ---- N5：错误响应含 evidenceRef ----
def test_n5_error_response_has_evidence_ref() -> None:
    """N5：M05 错误响应必须带 evidenceRef 槽位（openapi 契约）。"""
    from services.control_plane.api.server import _m05_error

    body = _m05_error("INPUT_INVALID", "ATTACHMENT_TOO_LARGE")
    assert "evidenceRef" in body


# ---- N4：幂等键唯一索引（真实 PG 迁移）----
def test_n4_idem_key_unique_index_in_migration() -> None:
    """N4：0003 迁移必须有 (user_id, session_id, idem_key) 唯一索引。"""
    from pathlib import Path

    migrations = Path("services/control_plane/migrations/versions")
    text = "".join(
        p.read_text(encoding="utf-8") for p in migrations.glob("000[34]_m05*.py")
    )
    assert "unique=True" in text and "idem_key" in text


# ---- A14：AttachmentInsight Pydantic 严格校验 ----
def test_a14_insight_strict_schema() -> None:
    """A14：insight_quality 越界 / 缺字段必须被 Pydantic 拒绝。"""
    from pydantic import ValidationError

    from services.control_plane.attachments.service import AttachmentInsight

    ok = AttachmentInsight(
        source_ref="att/x#sha256:abc", extracted={}, insight_quality=0.5
    )
    assert ok.insight_quality == 0.5
    with pytest.raises(ValidationError):
        AttachmentInsight(source_ref="att/x", extracted={}, insight_quality=1.5)


# ---- A18：未注册 role 写告警事件 ----
def test_a18_unregistered_role_alerts() -> None:
    """A18：伪造 role 被拒且写告警（事件通道），registry 不变。"""
    from services.control_plane.attachments.events import InMemoryEventSink
    from services.control_plane.attachments.processors import (
        UnregisteredRoleError,
        dispatch,
    )

    sink = InMemoryEventSink()
    with pytest.raises(UnregisteredRoleError):
        dispatch("fake_role", "image", "att/x", alert_sink=sink)
    assert any(e["kind"] == "alert" for e in sink.events)


# ---- V1：FAILED 上传必须 HTTP 非 2xx + evidenceRef ----
def test_v1_failed_upload_returns_error_status() -> None:
    """V1：处理器超时 → HTTP 500 + evidenceRef，不能伪装成 200/OK。"""
    from fastapi.testclient import TestClient

    from services.control_plane.accounts.service import AuthService
    from services.control_plane.accounts.stores import InMemoryUserStore
    from services.control_plane.api.server import create_app
    from services.control_plane.sessions.stores import InMemorySessionStore, SessionRow

    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    auth = AuthService(users, "a-very-long-test-secret-key-for-hs256-32bytes")
    reg = auth.register("fail@m05.test", "password123")
    user = users.find_by_email("fail@m05.test")
    assert user is not None
    sessions.insert(SessionRow(id="s1", user_id=user.id, state="DRAFT"))
    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({user.id: ["s1"]}),
    )
    svc.fail_next_dispatch = True
    app = create_app(
        users,
        sessions,
        "a-very-long-test-secret-key-for-hs256-32bytes",
        attachment_service=svc,
    )
    resp = TestClient(app).post(
        "/api/attachments",
        headers={"Authorization": f"Bearer {reg.access_token}"},
        data={"session_id": "s1", "description": "把这张图当背景"},
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert resp.status_code >= 400
    assert resp.json().get("evidenceRef")


# ---- V2：事件 sink 故障不能让上传 500（best-effort）----
def test_v2_event_failure_does_not_break_upload() -> None:
    """V2：事件 sink 抛错必须被吞掉（best-effort），上传仍成功。"""

    class BrokenSink:
        def emit(self, event: dict[str, object]) -> None:
            raise RuntimeError("sink down")

        def next_seq(self) -> int:
            return 1

    svc = AttachmentService(
        InMemoryObjectStore(),
        InMemoryAttachmentStore(),
        FakeTenantGuard({"u1": ["s1"]}),
        event_sink=BrokenSink(),
    )
    r = svc.upload("u1", "s1", _PNG, "image", "image/png", "把这张图当背景")
    assert r.insight_ref  # 上传成功，未被事件故障拖垮


# ---- V4：PG 并发同键不产生两份行（IntegrityError 折叠）----
def test_v4_idem_integrity_error_folds_to_existing() -> None:
    """V4：insert 抛唯一键冲突时，服务折叠为复用既有行（不 500）。"""
    from services.control_plane.attachments.stores import AttachmentRow

    class DupStore(InMemoryAttachmentStore):
        """第二次 insert 模拟 PG 唯一索引冲突。"""

        def __init__(self) -> None:
            super().__init__()
            self.raise_on_insert = False

        def insert(self, row: AttachmentRow) -> None:
            if self.raise_on_insert:
                raise ValueError("UNIQUE constraint failed: uq_attachments_idem")
            super().insert(row)

    store = DupStore()
    existing = AttachmentRow(
        id="winner",
        user_id="u1",
        session_id="s1",
        kind="image",
        mime="image/png",
        size=1,
        object_key="att/u1/s1/winner/original",
        hash="h",
        idem_key="s1:desc:abc",
    )
    store.insert(existing)
    store.raise_on_insert = True
    svc = AttachmentService(
        InMemoryObjectStore(), store, FakeTenantGuard({"u1": ["s1"]})
    )
    loser = AttachmentRow(
        id="loser",
        user_id="u1",
        session_id="s1",
        kind="image",
        mime="image/png",
        size=1,
        object_key="att/u1/s1/loser/original",
        hash="h",
        idem_key="s1:desc:abc",
    )
    folded = svc._insert_or_reuse(loser, "u1", "s1")
    assert folded.id == "winner"


# ---- R-1：撞唯一键且既有行是 FAILED → 复用该行重试，不 500 ----
def test_r1_failed_row_conflict_retries() -> None:
    """R-1：FAILED 行 + 唯一索引时，重试应更新该行而非永久 500。"""
    from services.control_plane.attachments.stores import AttachmentRow

    class ConflictStore(InMemoryAttachmentStore):
        def __init__(self) -> None:
            super().__init__()
            self.conflict = False
            self.updated: list[AttachmentRow] = []

        def insert(self, row: AttachmentRow) -> None:
            if self.conflict:
                raise ValueError("UNIQUE constraint failed: uq_attachments_idem")
            super().insert(row)

        def update(self, row: AttachmentRow) -> None:
            self.updated.append(row)
            super().update(row)

    store = ConflictStore()
    failed = AttachmentRow(
        id="failed-one",
        user_id="u1",
        session_id="s1",
        kind="image",
        mime="image/png",
        size=1,
        object_key="att/u1/s1/failed-one/original",
        hash="h",
        idem_key="s1:desc:abc",
        processing_state="FAILED",
    )
    store.insert(failed)
    store.conflict = True
    svc = AttachmentService(
        InMemoryObjectStore(), store, FakeTenantGuard({"u1": ["s1"]})
    )
    fresh = AttachmentRow(
        id="retry",
        user_id="u1",
        session_id="s1",
        kind="image",
        mime="image/png",
        size=1,
        object_key="att/u1/s1/retry/original",
        hash="h",
        idem_key="s1:desc:abc",
        processing_state="STORED",
    )
    folded = svc._insert_or_reuse(fresh, "u1", "s1")
    assert folded.id == failed.id
    assert store.updated  # 既有 FAILED 行被更新为重试态


# ---- R-2：seq 必须 per-session（不与 M04 会话 seq 冲突）----
def test_r2_seq_is_per_session() -> None:
    """R-2：RedisEventSink 的 seq 前缀必须含 session_id。"""

    from services.control_plane.attachments.events import RedisEventSink

    # 用假 Redis 验证：不同 session 的 next_seq 互不共享计数器
    class FakeRedis:
        def __init__(self) -> None:
            self.counters: dict[str, int] = {}

        def incr(self, key: str) -> int:
            self.counters[key] = self.counters.get(key, 0) + 1
            return self.counters[key]

    sink = RedisEventSink(bus=object(), redis_url="redis://unused")
    sink._redis = FakeRedis()
    s1a = sink.next_seq("s1")
    s2a = sink.next_seq("s2")
    s1b = sink.next_seq("s1")
    assert (s1a, s2a, s1b) == (1, 1, 2)  # per-session 单调
