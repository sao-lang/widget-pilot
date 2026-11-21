"""M07 偏好学习领域规则（ADR-002 D-10/D-11，纯代码零模型）。

四操作冲突裁定（写入时）、检索期衰减（只降权不删）、高置信门禁、程序记忆采纳阈值。
本模块是纯函数集合：不读时钟（由调用方注入 ``now``）、不调模型（I-M2）、不碰存储，
便于确定性单测与 PG Store 复用。
"""
from __future__ import annotations

from datetime import UTC, datetime

from packages.agent_core.types import JsonValue
from services.orchestrator.memory.types import (
    DECAY_RECENT_DAYS,
    DECAY_RECENT_FACTOR,
    DECAY_STALE_DAYS,
    DECAY_STALE_FACTOR,
    HIGH_CONFIDENCE_EVIDENCE_COUNT,
    HIGH_CONFIDENCE_WEIGHT,
    PROCEDURE_ADOPT_MIN,
    SOURCE_WEIGHT,
    FieldEvidence,
    PreferenceDecision,
    PreferenceField,
    PreferenceSource,
)


def decide_operation(
    existing: FieldEvidence | None,
    incoming_source: PreferenceSource,
    new_value: JsonValue | None = None,
) -> PreferenceDecision:
    """按写入时权重裁定四操作（D-10；低权重永不覆盖高权重，I-M3）。

    Args:
        existing: 字段当前证据；``None`` 表示字段为空。
        incoming_source: 新信号的来源（决定其固定权重）。
        new_value: 新字段值；与既有值相同则 ``NOOP``（D-10「字段值无变化」）。
            ``None`` 表示调用方不比较值（兼容仅需权重裁定的场景）。

    Returns:
        ``ADD`` / ``NOOP``（值无变化或权重更低）/ ``UPDATE``（新权重更高）。
        本纯函数不处理 ``DELETE``（用户清除是独立入口）。
    """
    if existing is None:
        return PreferenceDecision.ADD
    # 字段值无变化 → NOOP（D-10；仅在提供 new_value 时判定）。
    if new_value is not None and new_value == existing.value:
        return PreferenceDecision.NOOP
    incoming_weight = SOURCE_WEIGHT[incoming_source]
    # 同权重视为取新（UPDATE）；仅当新权重严格更低才 NOOP（I-M3）。
    if incoming_weight < existing.weight:
        return PreferenceDecision.NOOP
    return PreferenceDecision.UPDATE


def is_high_confidence(evidence: FieldEvidence) -> bool:
    """D-11 高置信门禁：``weight >= 0.7`` 或 ``evidence_count >= 2``。"""
    return (
        evidence.weight >= HIGH_CONFIDENCE_WEIGHT
        or evidence.evidence_count >= HIGH_CONFIDENCE_EVIDENCE_COUNT
    )


def _parse_iso(value: str) -> datetime:
    """解析证据时间戳；非法值回退 epoch（fail-safe，不因脏数据中断读取）。"""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def effective_weight(
    base_weight: float,
    updated_at: str,
    *,
    now: str | None = None,
) -> float:
    """检索期衰减（D-11）：近期 ×1.5、陈旧 ×0.3，只影响排序不删除。

    Args:
        base_weight: 字段证据基础权重。
        updated_at: 证据最后更新时刻（ISO 串）。
        now: 注入的当前时刻（ISO 串）；``None`` 用系统 UTC（仅生产装配用）。

    Returns:
        衰减后的生效权重（不裁剪到 1 以上，供排序与入选判定）。
    """
    now_dt = _parse_iso(now) if now else datetime.now(UTC)
    updated = _parse_iso(updated_at)
    age_days = (now_dt - updated).days
    age_days = max(age_days, 0)  # 时钟漂移保护：未来时间戳按当天处理
    if age_days <= DECAY_RECENT_DAYS:
        return base_weight * DECAY_RECENT_FACTOR
    if age_days > DECAY_STALE_DAYS:
        return base_weight * DECAY_STALE_FACTOR
    return base_weight


def rank_evidence(
    items: list[tuple[PreferenceField, FieldEvidence]],
    *,
    now: str | None = None,
) -> list[tuple[PreferenceField, FieldEvidence]]:
    """按生效权重降序排列高置信字段（字段名为平局裁决，保证确定性）。"""
    high = [(f, e) for f, e in items if is_high_confidence(e)]

    def key(item: tuple[PreferenceField, FieldEvidence]) -> tuple[float, str]:
        field_name, evidence = item
        weight = effective_weight(evidence.weight, evidence.updated_at, now=now)
        return (-weight, str(field_name))

    return sorted(high, key=key)


def should_inject_procedure(adopt_count: int) -> bool:
    """D-14：采纳次数达阈值才作为第 2 槽软提示（不改写规程资产）。"""
    return adopt_count >= PROCEDURE_ADOPT_MIN
