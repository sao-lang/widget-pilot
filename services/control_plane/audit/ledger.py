"""M04 AuditEvent 只追加账本协议与规范化（§1.2 R10 / §7 / §8.2 A18）。

hash 链防篡改：每条 prev_hash = 上一条的 chain_hash；只追加不更新不删除。
生产实现为 PG（pg_ledger.py）；内存账本仅存在于 `tests/support/control_plane.py`。
M04 产生原始事件，M11 汇总治理（§6.1 归属标注）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

# 审计事件形态含外部 payload（JSON 边界），用具名别名集中一处（编码规则 3）
type AuditPayload = dict[str, str | int | bool | None]


@runtime_checkable
class AuditLedgerPort(Protocol):
    """审计账本写入口：生产 PG 账本与测试内存账本共同满足（B2 生产接线）。"""

    def append(
        self,
        user_id: str,
        session_id: str,
        action: str,
        decision: str,
        payload: AuditPayload,
        trace_id: str,
    ) -> AuditEventRow: ...


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
