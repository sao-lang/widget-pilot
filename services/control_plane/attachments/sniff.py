"""magic number 内容嗅探（§3.4 A）。"""

from __future__ import annotations

# 结构性字面量：签名表集中一处（编码规则 common §2 例外）
_PNG = bytes.fromhex("89504e470d0a1a0a")
_JPEG_PREFIX = bytes.fromhex("ffd8ff")
_GIF = b"GIF8"
_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"
_MP4_FTYP = b"ftyp"
_OCTET_STREAM = "application/octet-stream"


def sniff_mime(head: bytes) -> str:
    """按文件头字节识别真实 mime，不信声明值。

    Args:
        head: 文件前 N 字节（N=SNIFF_HEAD_BYTES）。

    Returns:
        识别到的 mime；未知返回 `application/octet-stream`。
    """
    # 判定顺序：固定签名前缀优先，再按容器格式（WEBP/MP4 有偏移标签），最后文本兜底。
    if head.startswith(_PNG):
        return "image/png"
    if head.startswith(_JPEG_PREFIX):
        return "image/jpeg"
    if head.startswith(_GIF):
        return "image/gif"
    if len(head) >= 12 and head.startswith(_WEBP_RIFF) and head[8:12] == _WEBP_TAG:
        return "image/webp"
    if len(head) >= 12 and head[4:8] == _MP4_FTYP:
        return "video/mp4"
    if _is_text(head):
        return "text/plain"
    return _OCTET_STREAM


def _is_text(head: bytes) -> bool:
    """判定前 N 字节是否为可打印 UTF-8 且无二进制控制字符（S2 定稿口径）。

    正文级判定交后续文本处理器；此处只做文件头门控，不解析全文（§3.4 A）。
    """
    try:
        decoded = head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    # 允许常见空白（换行/制表），其余控制字符视为二进制。
    return all(ch.isprintable() or ch in "\n\r\t" for ch in decoded)
