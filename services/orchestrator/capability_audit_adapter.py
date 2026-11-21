"""能力加载审计生产适配器（ADR-003）。

实现 agent_core.injection.CapabilityAudit 鸭子协议，把 loop 内 L1/L2 请求写进
capability_audit_log（迁移 0005 建表，一步到位用 capability_id/level 口径）。
复用 PgCapabilityAuditLogStore 的只追加 + PG 失败隔离，不让审计抖动击穿校验链。
"""
from __future__ import annotations

from services.guardrails.pg_stores import PgCapabilityAuditLogStore
from services.guardrails.telemetry import CapabilityAuditLogRow
from services.guardrails.types import HitDecision


class ProductionCapabilityAudit:
    """把能力请求写进 capability_audit_log 的生产审计器。"""

    def __init__(self, dsn: str) -> None:
        """注入与运行期其它 PG 存储一致的连接串。"""
        self._store = PgCapabilityAuditLogStore(dsn)

    def record(
        self,
        session_id: str,
        run_id: str,
        trace_id: str,
        capability_id: str,
        level: int,
        surface: str,
        decision: str,
        latency_ms: int,
    ) -> None:
        """追加一条能力审计（decision: LOADED / DENIED / 错误码）。"""
        # 除 LOADED 外一律记 REJECT（含 CONTRACT_MISMATCH 等业务失败），
        # 避免 decisions() 把失败低报为通过（双口径）。
        is_reject = decision != "LOADED"
        self._store.append(
            CapabilityAuditLogRow(
                session_id=session_id,
                run_id=run_id,
                capability_id=capability_id,
                level=level,
                turn_count=0,
                tokens=0,
                decision=HitDecision.REJECT if is_reject else None,  # 通过记 None（口径同旧表）
                reason=f"{decision}|surface={surface}|latency_ms={latency_ms}",
                trace_id=trace_id,
            )
        )
