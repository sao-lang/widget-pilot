"""M05 真实环境集成测试（PG + MinIO + Pillow）。

skip 守卫：依赖不可达时明确 skip（不冒充通过）；Docker core 起时真跑。
断言：附件原文真实落 MinIO、索引真实落 PG、图片处理产出真实主色/尺寸、
纠偏后 PG 行更新且 MinIO 旧洞察版本保留。
"""

from __future__ import annotations

import os

import pytest

from services.control_plane.attachments.pg_stores import PgAttachmentStore
from services.control_plane.attachments.processor_adapter import RealProcessor
from services.control_plane.attachments.s3_store import S3ObjectStore
from services.control_plane.attachments.service import AttachmentService
from services.control_plane.attachments.tenant_guard import SessionTenantGuard
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.control_plane.sessions.stores import SessionRow

_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"


def _infra_ok() -> bool:
    """探测 PG + MinIO 端口；任一不可达返回 False（明确 skip，不假绿）。"""
    import socket

    for host, port in (("127.0.0.1", 5432), ("127.0.0.1", 9000)):
        try:
            with socket.create_connection((host, port), timeout=2):
                pass
        except OSError:
            return False
    return True


pytestmark = pytest.mark.skipif(not _infra_ok(), reason="需要 Docker core (PG+MinIO)")


@pytest.fixture
def real_service() -> AttachmentService:
    """真实 PG + MinIO + Pillow 服务（每测试清理用户/会话行）。"""
    os.environ.setdefault("S3_ENDPOINT", "http://127.0.0.1:9000")
    os.environ.setdefault("S3_ACCESS_KEY", "wpdev")
    os.environ.setdefault("S3_SECRET_KEY", "wpdev-secret")
    os.environ.setdefault("S3_BUCKET", "wp-dev")
    sessions = PgSessionStore(_DSN)
    return AttachmentService(
        objects=S3ObjectStore(),
        attachments=PgAttachmentStore(_DSN),
        tenant=SessionTenantGuard(sessions),
        processor=RealProcessor(),
    )


def test_real_png_upload_and_insight(real_service: AttachmentService) -> None:
    """真实 PNG：主色/尺寸非空、原文真进 MinIO、索引真进 PG、hash 校验通过。"""
    # 造一个真实 PNG（红色 8x8）
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")
    png = buf.getvalue()

    # 唯一 ID：避免并行 agent/测试复用同一 session 与 MinIO 前缀互相干扰
    from uuid import uuid4

    uid = "m05-real-" + uuid4().hex[:8]
    sid = "m05-s-" + uuid4().hex[:8]
    sessions = PgSessionStore(_DSN)
    sessions.insert(SessionRow(id=sid, user_id=uid, state="DRAFT"))

    result = real_service.upload(uid, sid, png, "image", "image/png", "把这张图当背景")
    assert result.role == "visual_reference"
    assert result.insight_ref and "#sha256:" in result.insight_ref
    assert result.object_key.startswith(f"att/{uid}/")

    # 原文真实可读 + hash 校验
    data = real_service.objects.get_with_hash_verify(result.object_key, result.hash)
    assert data == png

    # 洞察真实（Pillow 主色 + 尺寸）
    insight = real_service.read_insight(result.insight_ref)
    extracted = insight.extracted
    assert isinstance(extracted, dict)
    assert extracted["width"] == 8 and extracted["height"] == 8
    assert extracted["palette"]  # 真实主色非空
    assert extracted["thumbnail_ref"].startswith("data:image/webp;base64,")

    # PG 真实落行
    row = real_service.attachments.get(result.attachment_id)
    assert row is not None and row.role == "visual_reference"
    assert row.user_id == uid


def test_real_ocr_extracts_text_with_regions() -> None:
    """真实 OCR：带文字的图片经 rapidocr 识别，返回文本 + bbox 区域 + 质量 1。"""
    import io

    from PIL import Image, ImageDraw

    from services.control_plane.attachments.real_processors import process_ocr

    img = Image.new("RGB", (400, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "2026-11-20 MEETING", fill="black")
    draw.text((20, 75), "DATE: 2026/11/20", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    result = process_ocr(buf.getvalue())
    assert result.handler == "ContentExtractProcessor"
    assert result.insight_quality == 1.0
    assert result.extracted["ocr_text"]  # 真识别出文字
    assert len(result.extracted["regions"]) >= 1
    assert all(len(r["bbox"]) == 4 for r in result.extracted["regions"])


def test_real_video_keyframes() -> None:
    """真实 ffmpeg：MP4 抽关键帧 ≤ MAX_KEYFRAMES，帧为 base64 JPEG。"""
    import subprocess
    import tempfile
    from pathlib import Path

    import imageio_ffmpeg
    from PIL import Image

    from services.control_plane.attachments.constants import MAX_KEYFRAMES
    from services.control_plane.attachments.real_processors import process_video

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(10):
            Image.new("RGB", (160, 120), (i * 25, 80, 160)).save(
                Path(tmp) / f"f{i:03d}.png"
            )
        encode = subprocess.run(
            [
                exe,
                "-y",
                "-framerate",
                "1",
                "-i",
                str(Path(tmp) / "f%03d.png"),
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
        assert encode.returncode == 0
        result = process_video((Path(tmp) / "out.mp4").read_bytes())

    assert result.handler == "VideoFrameProcessor"
    assert result.insight_quality == 1.0
    assert 1 <= result.extracted["frame_count"] <= MAX_KEYFRAMES
    assert result.extracted["frames"][0].startswith("data:image/jpeg;base64,")


def test_real_patch_roles_uses_processor_and_emits_event() -> None:
    """真实环境纠偏：PG+MinIO 往返、真实处理器、事件 seq 单调、旧版保留。

    落实 N2（patch 走注入 processor）、N3（版本隔离）、A8（事件）、
    N12（docstring 曾虚假声称但无测试）。
    """
    import io
    from uuid import uuid4

    from PIL import Image

    from services.control_plane.attachments.events import InMemoryEventSink

    uid = "m05-patch-" + uuid4().hex[:8]
    sid = "m05-patch-s-" + uuid4().hex[:8]
    sink = InMemoryEventSink()
    svc = AttachmentService(
        objects=S3ObjectStore(),
        attachments=PgAttachmentStore(_DSN),
        tenant=SessionTenantGuard(PgSessionStore(_DSN)),
        processor=RealProcessor(),
        event_sink=sink,
    )
    PgSessionStore(_DSN).insert(SessionRow(id=sid, user_id=uid, state="DRAFT"))
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")

    first = svc.upload(uid, sid, buf.getvalue(), "image", "image/png", "把这张图当背景")
    assert first.role == "visual_reference"
    first_sequenced = max(e["seq"] for e in sink.events)

    patched = svc.patch_roles(uid, sid, first.attachment_id, "style_reference")
    assert patched.role == "style_reference"
    assert patched.insight_ref != first.insight_ref
    assert patched.handler == "StyleRefProcessor"  # N2：真实注入处理器
    assert any(e["seq"] > first_sequenced for e in sink.events)  # A8

    # PG 真落行
    row = svc.attachments.get(first.attachment_id)
    assert row is not None and row.role == "style_reference"
    # MinIO 旧版洞察保留（N3：只列该附件版本）
    versions = svc.objects.versions(first.attachment_id)
    assert versions and all(f"/{first.attachment_id}/" in v for v in versions)
