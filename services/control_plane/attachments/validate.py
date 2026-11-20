"""传输层校验（§3.2 step 3–4）。"""

from __future__ import annotations

from dataclasses import dataclass

from services.control_plane.attachments.constants import (
    ALLOWED_KINDS,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_SESSION,
)
from services.control_plane.attachments.sniff import sniff_mime

_CODE_INPUT_INVALID = "INPUT_INVALID"

# 合法声明 mime 白名单（§3.4 A）：嗅探结果 text/plain 时可回显声明的文本 mime；
# 二进制签名严格逐字节比对，嗅探失败结果 octet-stream 不得作为声明值通过（S-4）。
_TEXT_MIMES: frozenset[str] = frozenset({"text/plain", "text/csv", "application/json"})
_DECLARED_MIMES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "video/mp4",
        *_TEXT_MIMES,
    }
)


@dataclass(slots=True, frozen=True)
class ValidateResult:
    """传输层校验结论（§4.3 内部接口）。"""

    ok: bool
    reason_code: str
    detail: str
    detail_detail: str = ""


def validate_transfer(
    declared_kind: str,
    declared_mime: str,
    size: int,
    head: bytes,
    session_count: int,
) -> ValidateResult:
    """四类传输层校验，顺序固定。

    Returns:
        ValidateResult；任一不过即 ok=False 且带具体判据。
    """
    # 校验顺序固定（§3.2 step3）：kind → size → 数量 → 嗅探，先命中即短路，
    # 保证同一输入每次得到同一判据（可归因）。
    # 校验顺序固定（§3.2 step3）：kind → 声明 mime → size → 数量 → 嗅探，命中即短路。
    if declared_kind not in ALLOWED_KINDS:
        return ValidateResult(False, _CODE_INPUT_INVALID, "ATTACHMENT_KIND_DENIED")
    # S-4：声明 mime 必须在白名单；octet-stream 是嗅探失败结果，不能作为声明值放行。
    if declared_mime not in _DECLARED_MIMES:
        return ValidateResult(False, _CODE_INPUT_INVALID, "ATTACHMENT_KIND_DENIED")
    if size < 0 or size > MAX_ATTACHMENT_BYTES:
        return ValidateResult(
            False,
            _CODE_INPUT_INVALID,
            "ATTACHMENT_TOO_LARGE",
            "MAX_ATTACHMENT_BYTES",
        )
    if session_count >= MAX_ATTACHMENTS_PER_SESSION:
        return ValidateResult(False, _CODE_INPUT_INVALID, "ATTACHMENT_COUNT_EXCEEDED")
    sniffed = sniff_mime(head)
    # B-1：文本嗅探只识别 text/plain；声明为合法文本 mime（csv/json）时视为一致。
    text_match = sniffed == "text/plain" and declared_mime in _TEXT_MIMES
    if sniffed != declared_mime and not text_match:
        return ValidateResult(
            False,
            _CODE_INPUT_INVALID,
            "ATTACHMENT_TYPE_FORGED",
            f"声明 {declared_mime}，实际 {sniffed}",
        )
    return ValidateResult(True, "", "")
