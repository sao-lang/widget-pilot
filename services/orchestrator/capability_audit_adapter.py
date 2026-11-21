"""能力加载审计生产适配器（ADR-003 未完成项3）。

实现 agent_core.injection.CapabilityAudit 鸭子协议，把 loop 内 L1/L2 请求写进
M06 的 need_skill_log（复用迁移 0008 新增 capability_id/level 列）。复用
PgNeedSkillLogStore 的只追加 + PG 失败隔离，不让审计抖动击穿校验链。
"""
from __future__ import annotations

from services.guardrails.pg_stores import PgNeedSkillLogStore
from services.guardrails.telemetry import NeedSkillLogRow
from services.guardrails.types import HitDecision


class ProductionCapabilityAudit:
    """把能力请求写进 need_skill_log 的生产审计器。"""

    def __init__(self, dsn: str) -> None:
        """注入与运行期其它 PG 存储一致的连接串。"""
        self._store = PgNeedSkillLogStore(dsn)

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
            NeedSkillLogRow(
                session_id=session_id,
                run_id=run_id,
                recipe_id=capability_id,  # 旧列承载能力句柄（语义扩展）
                depth=level,
                turn_count=0,
                tokens=0,
                decision=HitDecision.REJECT if is_reject else None,  # 与旧"通过=None"口径一致
                reason=f"{decision}|surface={surface}|latency_ms={latency_ms}",
                trace_id=trace_id,
                capability_id=capability_id,  # 新列（ADR-003）
                level=level,  # 新列（ADR-003）
            )
        )
