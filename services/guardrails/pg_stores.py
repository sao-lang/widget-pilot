"""M06 只追加日志的 PG 实现（M04 §9.0 接线义务 3）。

与 :mod:`services.guardrails.telemetry` 的内存 Store 同接口（append + 各自的
统计/归因方法），落 PG 后进程重启、新建实例仍可复算统计与归因链。只替换存储，
不重复判定逻辑（规则 8）。guard_hit 的 PG 实现在 guard_hit_pg.py（hash 链语义独立，不在此模块）。

表结构由迁移 ``0005_m06_telemetry`` 建立；Store 只通过 SQLAlchemy ORM 读写，不建表。
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import CapabilityAuditLog, IntentRouteLog
from services.guardrails.telemetry import CapabilityAuditLogRow, IntentRouteLogRow
from services.guardrails.types import HitDecision, RouteLayer

logger = logging.getLogger(__name__)

# 统计口径键名（结构性字面量，与内存实现的返回契约一致）。
_KEY_ESCALATION_RATE: Final = "escalation_rate"


class PgIntentRouteLogStore:
    """IntentRouteLog 的 PG 只追加存储：stats() 从库内记录复算各级占比。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短 Session，跨实例天然可读回）。

        Args:
            dsn: PostgreSQL 连接串（任意驱动前缀统一转 pg8000 同步）。
        """
        self._factory = make_session_factory(dsn)

    def append(self, row: IntentRouteLogRow) -> None:
        """追加一条意图裁定记录到 intent_route_log。

        留证写入失败须隔离——PG 抖动不能击穿校验链把正常业务打挂
        （否则 dispatcher 不 ACK→重试→死信）。只告警，不影响 ValidationResult。
        """
        try:
            with session_scope(self._factory) as session:
                session.add(
                    IntentRouteLog(
                        session_id=row.session_id,
                        run_id=row.run_id,
                        route_layer=row.route_layer.value,
                        intent=row.intent,
                        similarity=row.similarity,
                        latency_ms=row.latency_ms,
                        samples_version=row.samples_version,
                        trace_id=row.trace_id,
                        fallback_reason=row.fallback_reason,
                    )
                )
        except SQLAlchemyError as exc:
            logger.warning("intent_route_log append failed (evidence dropped): %s", exc)

    def stats(self) -> dict[str, float]:
        """从 PG 复算各级占比与升级率（L3+L4 占比），口径同内存实现。"""
        with session_scope(self._factory) as session:
            layers = session.scalars(select(IntentRouteLog.route_layer)).all()
        total = len(layers)
        if total == 0:
            return {layer.value: 0.0 for layer in RouteLayer} | {
                _KEY_ESCALATION_RATE: 0.0
            }
        counts: Counter[RouteLayer] = Counter(
            RouteLayer(str(layer)) for layer in layers
        )
        result = {layer.value: counts[layer] / total for layer in RouteLayer}
        result[_KEY_ESCALATION_RATE] = (
            counts[RouteLayer.L3] + counts[RouteLayer.L4]
        ) / total
        return result


class PgCapabilityAuditLogStore:
    """能力请求审计的 PG 只追加存储：decisions() 从库内记录复算决策计数。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短 Session，跨实例天然可读回）。"""
        self._factory = make_session_factory(dsn)

    def append(self, row: CapabilityAuditLogRow) -> None:
        """追加一条 L1/L2 能力请求记录到 capability_audit_log。

        留证失败隔离（同上，PG 抖动不击穿校验链）。
        """
        try:
            with session_scope(self._factory) as session:
                session.add(
                    CapabilityAuditLog(
                        session_id=row.session_id,
                        run_id=row.run_id,
                        capability_id=row.capability_id,
                        level=row.level,
                        turn_count=row.turn_count,
                        tokens=row.tokens,
                        decision=row.decision.value if row.decision is not None else None,
                        reason=row.reason,
                        trace_id=row.trace_id,
                    )
                )
        except SQLAlchemyError as exc:
            logger.warning("capability_audit_log append failed (evidence dropped): %s", exc)

    def decisions(self) -> Counter[HitDecision | None]:
        """按 decision 计数（含 None），口径同内存实现。"""
        with session_scope(self._factory) as session:
            values = session.scalars(select(CapabilityAuditLog.decision)).all()
        return Counter(
            HitDecision(str(value)) if value is not None else None for value in values
        )
