"""U-8：附件洞察槽的生产实现（M05 AttachmentInsight → M03/M07 上下文槽）。

零模型：消费的是 M05 已产出的洞察 JSON（CSV 统计 / 视频帧 / 本地 OCR / 图片
主色尺寸），不是现场调视觉端点。文本按数据段注入，超预算只留 object_key +
预览（与 AttachmentInsightPort 协议一致）。
"""
from __future__ import annotations

import json
import logging

from services.control_plane.attachments.ports import (
    AttachmentStorePort,
    ObjectStorePort,
)
from services.guardrails.output_ports import parse_object_ref

logger = logging.getLogger(__name__)

_MAX_PREVIEW_CHARS = 8_000


class StoreInsightPort:
    """按附件 id 读 insight_ref → 对象存储 JSON → 文本（生产端口实现）。"""

    def __init__(self, store: AttachmentStorePort, objects: ObjectStorePort) -> None:
        self._store = store
        self._objects = objects

    def insights(self, attachment_refs: list[str]) -> list[str]:
        out: list[str] = []
        for aid in attachment_refs:
            row = self._store.get(aid)
            if row is None or not row.insight_ref:
                continue
            try:
                key, digest = parse_object_ref(row.insight_ref)
                data = self._objects.get_with_hash_verify(key, digest)
                payload = json.loads(data.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - 洞察不可信/缺失，跳过不阻断生成
                logger.warning("attachment insight unreadable (%s): %s", aid, exc)
                continue
            if not isinstance(payload, dict):
                continue
            extracted = payload.get("extracted")
            body = json.dumps(extracted, ensure_ascii=False, default=str)
            prefix = f"[{aid}] insight_ref={row.insight_ref} "
            if len(body) > _MAX_PREVIEW_CHARS:
                body = body[:_MAX_PREVIEW_CHARS] + "…(truncated)"
            out.append(prefix + body)
        return out


__all__ = ["StoreInsightPort"]
