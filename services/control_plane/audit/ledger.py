"""M04 AuditEvent 只追加账本（§1.2 R10 / §7 / §8.2 A18）。

hash 链防篡改：每条 prev_hash = 上一条的 chain_hash；只追加不更新不删除。
M04 产生原始事件，M11 汇总治理（§6.1 归属标注）。内存实现供无 Docker 验收。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

# 审计事件形态含外部 payload（JSON 边界），用具名别名集中一处（编码规则 3）
type AuditPayload = dict[str, str | int | bool | None]


@dataclass(slots=True, frozen=True)
class AuditEventRow:
    """AuditEvent 表的一行（§6.1 字段）。"""

    seq: int
    """只追加序号。"""
    user_id: str
    """归属用户（租户隔离）。"""
    session_id: str
    """所属会话。"""
    action: str
    """动作（状态迁移/signal/越权拒绝等，有限取值见常量）。"""
    decision: str
    """判定结果。"""
    payload: AuditPayload
    """证据载荷（不含 PII 原文/附件原文/代码）。"""
    trace_id: str
    """链路追踪 id（供 M11 重放）。"""
    prev_hash: str
    """链上前一条 hash（首行为空串）。"""
    chain_hash: str
    """本条链 hash = sha256(prev_hash + 规范化 payload)。"""
    created_at: datetime
    """写入时间（UTC）。"""


class AuditActions:
    """审计动作常量（编码规则 5：语义字符串具名，禁散落裸串）。"""

    STATE_TRANSITION = "state_transition"
    """状态迁移。"""
    INTERRUPT = "interrupt"
    """打断。"""
    RESUME = "resume"
    """继续。"""
    CANCEL = "cancel"
    """取消。"""
    DENY = "deny"
    """越权拒绝。"""


def _canonical_body(
    user_id: str,
    session_id: str,
    action: str,
    decision: str,
    payload: AuditPayload,
    trace_id: str,
) -> str:
    """审计证据的规范化字节源（S6：全字段入链，sort_keys 保证可重算一致）。"""
    return json.dumps(
        {
            "user_id": user_id,
            "session_id": session_id,
            "action": action,
            "decision": decision,
            "payload": payload,
            "trace_id": trace_id,
        },
        sort_keys=True,
        default=str,
    )


class AuditLedger:
    """只追加审计账本（内存实现；生产为 PG append-only 表）。"""

    def __init__(self) -> None:
        """初始化空账本与链头。"""
        self._rows: list[AuditEventRow] = []

    def append(
        self,
        user_id: str,
        session_id: str,
        action: str,
        decision: str,
        payload: AuditPayload,
        trace_id: str,
    ) -> AuditEventRow:
        """追加一条审计事件，计算 prev_hash/chain_hash 成链。

        Args:
            user_id: 事件归属用户（成功迁移为资源 owner，DENY 为尝试者）。
            session_id: 所属会话。
            action: 动作（AuditActions 常量：state_transition/interrupt/deny 等）。
            decision: 判定结果（OK / reason_code / FORBIDDEN）。
            payload: 证据载荷，禁含 PII 原文/附件原文/代码。
            trace_id: 链路追踪 id（供 M11 重放）。

        Returns:
            写入的 AuditEventRow（含计算出的 prev_hash/chain_hash）。

        S6：整行证据字段参与 hash（不只 payload），篡改任一字段都能被 chain_valid 检出。
        """
        prev = self._rows[-1].chain_hash if self._rows else ""
        # S6：整行证据字段入链（不只 payload），篡改 action/user_id/decision 也能被发现（A18）
        body = _canonical_body(user_id, session_id, action, decision, payload, trace_id)
        chain = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
        row = AuditEventRow(
            seq=len(self._rows),
            user_id=user_id,
            session_id=session_id,
            action=action,
            decision=decision,
            payload=payload,
            trace_id=trace_id,
            prev_hash=prev,
            chain_hash=chain,
            created_at=datetime.now(UTC),
        )
        self._rows.append(row)
        return row

    def chain_valid(self) -> bool:
        """重算整条 hash 链，任一环断裂返回 False（A18：可完整重放）。

        Returns:
            True 表示链完整（每行 prev_hash 等于前行 chain_hash 且全字段 hash 吻合）。
        """
        prev = ""
        for row in self._rows:
            body = _canonical_body(
                row.user_id,
                row.session_id,
                row.action,
                row.decision,
                row.payload,
                row.trace_id,
            )
            expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            if row.prev_hash != prev or row.chain_hash != expected:
                return False
            prev = row.chain_hash
        return True

    def by_session(self, session_id: str) -> list[AuditEventRow]:
        """按会话查审计事件（供 M11 重放与 A18 断言）。

        Args:
            session_id: 会话标识。
        """
        return [r for r in self._rows if r.session_id == session_id]

    def all(self) -> list[AuditEventRow]:
        """返回全部事件（副本）。"""
        return list(self._rows)
