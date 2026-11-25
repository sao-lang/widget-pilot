"""U-8：附件洞察槽生产适配器（零模型，真 store 桩）。"""
from __future__ import annotations

import json
from typing import cast

from services.control_plane.attachments.ports import (
    AttachmentStorePort,
    ObjectStorePort,
)
from services.control_plane.attachments.stores import AttachmentRow

_MAX_PREVIEW = 8000


class _Store:
    def __init__(self, rows: dict[str, AttachmentRow]) -> None:
        self._rows = rows

    def get(self, attachment_id: str) -> AttachmentRow | None:
        return self._rows.get(attachment_id)


class _Objects:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get_with_hash_verify(self, key: str, expected_hash: str) -> bytes:
        import hashlib

        data = json.dumps(self.payload).encode()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("CONTRACT_MISMATCH: object hash mismatch")
        return data


def _row(aid: str, insight_hash: str = "b" * 64) -> AttachmentRow:
    return AttachmentRow(
        id=aid, user_id="u1", session_id="s1", kind="image", mime="image/png",
        size=10, object_key=f"att/u1/s1/{aid}/original", hash="a" * 64,
        insight_ref=f"att/u1/s1/{aid}/insight.json#sha256:" + insight_hash,
        insight_quality=0.9,
    )


def test_insights_returns_extracted_text() -> None:
    """按 id 读洞察 JSON，extracted 渲染为文本（数据段，不解析为指令）。"""
    from services.orchestrator.memory.attachment_adapter import StoreInsightPort

    payload = {
        "source_ref": "att/u1/s1/a1/original#sha256:" + "a" * 64,
        "extracted": {"dominant_colors": ["#fff"], "width": 100},
        "insight_quality": 0.9,
    }
    import hashlib

    digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    port = StoreInsightPort(cast(AttachmentStorePort, _Store({"a1": _row("a1", digest)})), cast(ObjectStorePort, _Objects(payload)))
    texts = port.insights(["a1"])
    assert len(texts) == 1
    assert "dominant_colors" in texts[0]
    assert "a1" in texts[0]


def test_insights_skips_missing_and_oversize() -> None:
    """无洞察 id 跳过；超长 extracted 截断留 object_key（不撑爆预算）。"""
    from services.orchestrator.memory.attachment_adapter import StoreInsightPort

    payload = {"extracted": {"text": "x" * (_MAX_PREVIEW * 2)}}
    import hashlib

    digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    port = StoreInsightPort(cast(AttachmentStorePort, _Store({"a1": _row("a1", digest)})), cast(ObjectStorePort, _Objects(payload)))
    texts = port.insights(["missing", "a1"])
    assert len(texts) == 1
    assert len(texts[0]) <= _MAX_PREVIEW + 200
    assert "insight.json" in texts[0]  # 留 object_key 供回放


def test_insight_reaches_assembled_messages() -> None:
    """端到端：Input.attachment_port → SlotConfig → assemble → system message。"""
    from typing import cast

    from packages.agent_core.context.ports import AttachmentInsightPort
    from packages.agent_core.context.slots import SlotConfig, assemble
    from services.orchestrator.memory.attachment_adapter import StoreInsightPort

    payload = {"extracted": {"summary": "白底圆形图案"}}
    import hashlib

    digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    port = StoreInsightPort(
        cast(AttachmentStorePort, _Store({"a1": _row("a1", digest)})),
        cast(ObjectStorePort, _Objects(payload)),
    )
    from packages.agent_core.types import State

    messages = assemble(
        State.initial("s1", "r1", "描述"),
        SlotConfig(
            attachment_port=cast(AttachmentInsightPort, port),
            attachment_refs=("a1",),
        ),
    )
    texts = [str(getattr(m, "content", "")) for m in messages]
    assert any("白底圆形图案" in t for t in texts)


def test_insight_tampered_hash_drops_slot() -> None:
    """N1：洞察对象 hash 不符 → 跳过该附件（不把篡改内容注入 prompt）。"""
    from typing import cast

    from services.orchestrator.memory.attachment_adapter import StoreInsightPort

    payload = {"extracted": {"summary": "应被丢弃"}}
    port = StoreInsightPort(
        cast(AttachmentStorePort, _Store({"a1": _row("a1", "0" * 64)})),
        cast(ObjectStorePort, _Objects(payload)),
    )
    assert port.insights(["a1"]) == []
