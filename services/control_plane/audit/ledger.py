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
        """追加一条审计事件，计算 prev_hash/chain_hash 成链。"""
        prev = self._rows[-1].chain_hash if self._rows else ""
        # 规范化payload（sort_keys）保证 hash 可重算一致（A18）
        body = json.dumps(payload, sort_keys=True, default=str)
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
        """重算整条 hash 链，任一环断裂返回 False（A18：可完整重放）。"""
        prev = ""
        for row in self._rows:
            body = json.dumps(row.payload, sort_keys=True, default=str)
            expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            if row.prev_hash != prev or row.chain_hash != expected:
                return False
            prev = row.chain_hash
        return True

    def by_session(self, session_id: str) -> list[AuditEventRow]:
        """按会话查审计事件（供 M11 重放与 A18 断言）。"""
        return [r for r in self._rows if r.session_id == session_id]

    def all(self) -> list[AuditEventRow]:
        """返回全部事件（副本）。"""
        return list(self._rows)
