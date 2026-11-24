"""M09 → M10 语料联动的生产桥接（D-1：用户删除控件 → 公共语料立即失效）。

M10 入库门禁/语料撤回执行体尚未落地（见 docs/modules/M10-知识检索与评测域.md
§9 与 M09 §9.1）。本实现不是内存桩：它把删除事件以**只追加审计**方式落到
真实 AuditLedgerPort（生产 PG），作为 M10 撤回消费的权威持久记录——M10 接线后
改为直接调其撤回 API，本桥接保留审计留证。缺口登记在 M09 模型/功能缺口文档。
"""
from __future__ import annotations

from services.control_plane.audit.ledger import AuditLedgerPort
from services.control_plane.bundles.ports import CorpusLinkPort

_ACTION_CORPUS_INVALIDATE = "bundle_corpus_invalidate"
"""语料失效联动审计动作（M10 消费锚点）。"""


class AuditBackedCorpusLink:
    """生产语料联动：删除事件写只追加审计，供 M10 撤回消费（无内存态）。"""

    def __init__(self, audit: AuditLedgerPort) -> None:
        """注入真实审计账本（生产 PgAuditLedger）。"""
        self._audit = audit

    def on_bundle_deleted(self, bundle_id: str) -> None:
        """记录一次语料失效请求（不带 PII / 代码，只带 bundle_id）。"""
        self._audit.append(
            "",
            "",
            _ACTION_CORPUS_INVALIDATE,
            bundle_id,
            {"bundle_id": bundle_id},
            "",
        )


def make_corpus_link(audit: AuditLedgerPort) -> CorpusLinkPort:
    """生产工厂：构造语料联动实现（缺 audit 由调用方 fail-fast）。"""
    return AuditBackedCorpusLink(audit)
