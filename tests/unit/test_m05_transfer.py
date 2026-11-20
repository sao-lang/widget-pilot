"""M05 传输层纯函数验收（§8.2 A3–A6 / §3.4 A 嗅探签名表）。

TDD RED：先写期望行为，模块尚未实现 → import 失败即正确红。
"""

from __future__ import annotations

import pytest

from services.control_plane.attachments.constants import (
    ALLOWED_KINDS,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_SESSION,
)
from services.control_plane.attachments.sniff import sniff_mime
from services.control_plane.attachments.validate import validate_transfer

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8
_JPEG = bytes.fromhex("ffd8ff") + b"\x00" * 13
_EXE = b"MZ" + b"\x00" * 14
_TEXT = b"date,value\n2026-01-01,42\n"


def test_a3_forged_extension_rejected() -> None:
    """A3：声明 image/png 实际是 MZ（exe）→ sniff 为 application/octet-stream，校验拒绝。"""
    assert sniff_mime(_EXE) == "application/octet-stream"
    result = validate_transfer("image", "image/png", len(_EXE), _EXE, 0)
    assert result.ok is False
    assert result.detail == "ATTACHMENT_TYPE_FORGED"


def test_a4_oversize_rejected_with_reason() -> None:
    """A4：size 超过上限 → 拒绝且给具体判据，不进嗅探。"""
    result = validate_transfer("image", "image/png", MAX_ATTACHMENT_BYTES + 1, _PNG, 0)
    assert result.ok is False
    assert result.detail == "ATTACHMENT_TOO_LARGE"
    assert "MAX_ATTACHMENT_BYTES" in result.detail_detail


def test_a5_count_exceeded_rejected() -> None:
    """A5：会话附件数已达上限 → 拒绝。"""
    result = validate_transfer(
        "image", "image/png", len(_PNG), _PNG, MAX_ATTACHMENTS_PER_SESSION
    )
    assert result.ok is False
    assert result.detail == "ATTACHMENT_COUNT_EXCEEDED"


def test_a6_kind_not_whitelisted_rejected() -> None:
    """A6：kind 不在 {image,video,text} → 拒绝，detail 为 KIND_DENIED。"""
    assert ALLOWED_KINDS == frozenset({"image", "video", "text"})
    result = validate_transfer(
        "application/x-msdownload", "image/png", len(_PNG), _PNG, 0
    )
    assert result.ok is False
    assert result.detail == "ATTACHMENT_KIND_DENIED"


def test_valid_png_passes() -> None:
    """合法 PNG：四项全过。"""
    result = validate_transfer("image", "image/png", len(_PNG), _PNG, 1)
    assert result.ok is True
    assert result.detail == ""


def test_check_order_kind_before_size() -> None:
    """校验顺序固定：kind 非法优先于 size 超限（判据可归因）。"""
    result = validate_transfer("bad", "image/png", MAX_ATTACHMENT_BYTES + 1, _PNG, 99)
    assert result.detail == "ATTACHMENT_KIND_DENIED"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (_PNG, "image/png"),
        (_JPEG, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 10, "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4, "image/webp"),
        (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4, "video/mp4"),
        (_TEXT, "text/plain"),
    ],
)
def test_sniff_signatures(head: bytes, expected: str) -> None:
    """嗅探签名表：各声明类型的真实文件头被正确识别。"""
    assert sniff_mime(head) == expected
