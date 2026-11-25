"""P0 附件生成侧：把 Input.attachments 的字符串 id 解析为工具链可用富对象。

capability_planner 从附件对象取 ``source_ref`` / ``id`` / ``kind``；但 Input
只带 id 字符串。本模块用附件索引 store 读行，拼出带 hash 的 source_ref
（与 attachments/service.py 同口径），补齐类型断点。查不到的 id 跳过，
不伪造产物。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from services.control_plane.attachments.ports import AttachmentStorePort


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """工具链视角的附件引用（字段与 tests/support/m08._Att 同构）。"""

    id: str
    kind: str
    mime: str
    source_ref: str
    insight_ref: str = ""


class _Getter(Protocol):
    def get(self, attachment_id: str) -> object: ...


def resolve_attachments(ids: list[str], store: AttachmentStorePort | _Getter) -> list[AttachmentRef]:
    """按 id 查 store，产出带 sha256 source_ref 的富对象（缺失跳过）。"""
    out: list[AttachmentRef] = []
    for aid in ids:
        row = store.get(aid)
        if row is None:
            continue
        object_key = str(getattr(row, "object_key", "") or "")
        digest = str(getattr(row, "hash", "") or "")
        if not object_key or len(digest) != 64:
            continue  # 无 key / 非法 hash 不伪造 ref
        out.append(AttachmentRef(
            id=aid,
            kind=str(getattr(row, "kind", "") or ""),
            mime=str(getattr(row, "mime", "") or ""),
            source_ref=f"{object_key}#sha256:{digest}",
            insight_ref=str(getattr(row, "insight_ref", "") or ""),
        ))
    return out
