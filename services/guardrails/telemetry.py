"""M06 只追加日志的行结构（§6.1 / I8 / I15）。

生产存储为 pg_stores.py 的三张 PG 表；内存替身位于 tests/support/guardrails.py。
统计从日志复算、不是预置常量——支撑 A25「各级命中率可从 IntentRouteLog 复算」。
"""

from __future__ import annotations

from dataclasses import dataclass

from services.guardrails.types import HitDecision, RouteLayer


@dataclass(slots=True, frozen=True)
class IntentRouteLogRow:
    """一次意图裁定留证（§6.1 IntentRouteLog）。"""

    session_id: str
    run_id: str
    route_layer: RouteLayer
    intent: str
    similarity: float | None
    latency_ms: int
    samples_version: str
    trace_id: str = ""
    fallback_reason: str | None = None  # 降级归因（L2/L3 端点不可用或不可解析）


@dataclass(slots=True, frozen=True)
class NeedSkillLogRow:
    """一次 need_skill 决策留证（§6.1 NeedSkillLog）。"""

    session_id: str
    run_id: str
    recipe_id: str
    depth: int
    turn_count: int
    tokens: int
    decision: HitDecision | None
    reason: str | None
    trace_id: str = ""
    capability_id: str | None = None
    """统一能力句柄（ADR-003）；旧 need_skill 记录为 None，新记录填 id。"""
    level: int | None = None
    """披露层级 1=详情 / 2=执行或正文（ADR-003）；旧记录为 None。"""
