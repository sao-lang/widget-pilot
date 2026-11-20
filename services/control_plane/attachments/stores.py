"""M05 附件实体与对象引用结构（§4.3 / §6.1）。

生产存储为 S3ObjectStore（对象）+ PgAttachmentStore（索引）；
内存替身位于 `tests/support/attachments.py`，源码不保留测试桩。
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.enums import AttachmentRole


@dataclass(slots=True, frozen=True)
class ObjectRef:
    """对象引用（§4.3 put_original 返回）。"""

    key: str
    hash: str
    size: int


@dataclass(slots=True)
class AttachmentRow:
    """Attachment 实体（§6.1，v1.7 S1 拆双 confidence）。"""

    id: str
    user_id: str
    session_id: str
    kind: str
    mime: str
    size: int
    object_key: str
    hash: str
    role: AttachmentRole | str | None = None
    role_confidence: float = 0.0
    insight_ref: str | None = None
    insight_quality: float = 0.0
    handler: str = ""
    processing_state: str = "RECEIVED"
    clarify_hint: str = ""
    idem_key: str = ""
    evidence_ref: str = ""
