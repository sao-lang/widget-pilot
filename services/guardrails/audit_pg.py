"""M06 输入快照审计的 PG 适配器（§2.4 / A1）。

复用 M04 的 AuditEvent 只追加表与 hash 链，避免再引入第二套持久化；
AuditEvent（session/run/evidence_ref/payload_hash）映射为审计 payload 标量。
"""

from __future__ import annotations

from typing import Final

from services.control_plane.audit.ledger import AuditPayload
from services.control_plane.audit.pg_ledger import PgAuditLedger
from services.guardrails.audit import AuditEvent

_ACTOR: Final[str] = "system"
"""无真实 user_id 的护栏审计统一 actor（M04 表 actor 非空）。"""

_DECISION_OK: Final[str] = "OK"
"""护栏审计只记录已生成的证据引用，不做额外放行裁决。"""


class PgGuardrailAuditStore:
    """AuditStorePort 的生产实现：写入 M04 AuditEvent PG 表。

    Attributes:
        _ledger: 复用的 M04 PG 审计账本（自带 hash 链与 advisory lock）。
    """

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN，构造底层账本。"""
        self._ledger = PgAuditLedger(dsn)

    def append(self, event: AuditEvent) -> None:
        """把一条护栏审计事件追加到 PG。

        Args:
            event: M06 输入快照审计事件（evidenceRef 已带 sha256）。
        """
        payload: AuditPayload = {
            "run_id": event.run_id,
            "evidence_ref": event.evidence_ref,
            "payload_hash": event.payload_hash,
        }
        self._ledger.append(
            _ACTOR,
            event.session_id,
            event.kind,
            _DECISION_OK,
            payload,
            event.trace_id,
        )
