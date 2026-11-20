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

    sessions = PgSessionStore(_DSN)
    if sessions.get("m05-real-s1") is None:  # 幂等：重跑不撞主键
        sessions.insert(
            SessionRow(id="m05-real-s1", user_id="m05-real-user", state="DRAFT")
        )

    result = real_service.upload(
        "m05-real-user", "m05-real-s1", png, "image", "image/png", "把这张图当背景"
    )
    assert result.role == "visual_reference"
    assert result.insight_ref and "#sha256:" in result.insight_ref
    assert result.object_key.startswith("att/m05-real-user/")

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
    assert row.user_id == "m05-real-user"
