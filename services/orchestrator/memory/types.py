"""M07 记忆领域模型（v1.6，ADR-002 D-9~D-14）。

三层记忆 + 轻量程序记忆的纯数据结构：语义记忆 ``UserPreference``（每字段证据化）、
情节记忆 ``PreferenceEvent``（只追加、失效不删行）、程序记忆 ``UserProcedure``
（零 LLM 采纳计数）。本模块只定义结构，不依赖 PG / 任何服务，便于领域规则与存储解耦。

类型约定：确定字段显式声明；JSON 边界用 ``JsonValue``；零裸 ``Any``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from packages.agent_core.types import JsonValue

# --- 具名常量（禁魔法值）---------------------------------------------------

PREFERENCE_VERSION_INITIAL: int = 1
"""用户偏好初始版本号（首次写入从 1 起）。"""
DECAY_RECENT_DAYS: int = 30
"""近期强化窗口（天）：窗口内证据权重乘 ``DECAY_RECENT_FACTOR``。"""
DECAY_RECENT_FACTOR: float = 1.5
"""近期强化系数（ADR-002 D-11）。"""
DECAY_STALE_DAYS: int = 180
"""陈旧窗口（天）：超过则证据权重乘 ``DECAY_STALE_FACTOR``，只降权不删除。"""
DECAY_STALE_FACTOR: float = 0.3
"""陈旧降权系数（ADR-002 D-11）。"""
EVENT_TTL_DAYS: int = 180
"""情节记忆 TTL（天）：到期失效不删行。"""
HIGH_CONFIDENCE_WEIGHT: float = 0.7
"""高置信门禁权重阈值（D-11）：``weight >= 0.7`` 或 ``evidence_count >= 2``。"""
HIGH_CONFIDENCE_EVIDENCE_COUNT: int = 2
"""高置信门禁证据数阈值（D-11）。"""
PROCEDURE_ADOPT_MIN: int = 2
"""程序记忆注入的最小采纳次数（D-14）：``adopt_count >= 2`` 才作为软提示。"""


class PreferenceField(StrEnum):
    """可学习的偏好字段（白名单，防止学进 PII / 历史代码）。"""

    PALETTE = "palette"
    PREFERRED_GRIDS = "preferred_grids"
    PREFERRED_WIDGET_TYPES = "preferred_widget_types"


class PreferenceSource(StrEnum):
    """写入信号来源（D-10 四类白名单）。"""

    EXPLICIT = "explicit"
    CLARIFICATION = "clarification"
    CORRECTION = "correction"
    ADOPTED_ARTIFACT = "adopted_artifact"


class PreferenceDecision(StrEnum):
    """写入四操作决策（D-10，写入时裁定冲突）。"""

    ADD = "ADD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    NOOP = "NOOP"


SOURCE_WEIGHT: dict[PreferenceSource, float] = {
    PreferenceSource.EXPLICIT: 1.0,
    PreferenceSource.CLARIFICATION: 0.8,
    PreferenceSource.CORRECTION: 0.7,
    PreferenceSource.ADOPTED_ARTIFACT: 0.3,
}
"""各信号来源的固定权重（D-10；低权重永不覆盖高权重，I-M3）。"""


@dataclass(frozen=True)
class FieldEvidence:
    """单个偏好字段的证据元数据。

    Attributes:
        value: 字段当前值（JSON 边界：枚举字符串或短字符串列表）。
        source: 最后一次有效写入的来源。
        weight: 当前生效权重（0~1，衰减后可能低于 source 基础权重）。
        evidence_count: 有效证据累计次数。
        updated_at: 最后更新时刻（UTC ISO 字符串）。
    """

    value: JsonValue
    source: PreferenceSource
    weight: float
    evidence_count: int
    updated_at: str


@dataclass
class UserPreference:
    """语义记忆：用户偏好聚合（每字段证据化 + 全局版本）。

    Attributes:
        user_id: 租户隔离键。
        fields: 字段名 → 证据元数据；只含白名单 ``PreferenceField``。
        version: 全局版本号，任一有效写入 ``+1``，进复现指纹（I-M6）。
    """

    user_id: str
    fields: dict[PreferenceField, FieldEvidence] = field(default_factory=dict)
    version: int = PREFERENCE_VERSION_INITIAL


@dataclass(frozen=True)
class PreferenceEvent:
    """情节记忆：只追加的写入证据（失效不删行，D-13）。

    Attributes:
        user_id: 租户隔离键。
        session_id: 触发会话（显式写入可为空）。
        field_name: 被写入的偏好字段。
        old_value: 写入前字段值（None 表示原本为空）。
        new_value: 写入后的字段值。
        source: 写入信号来源。
        source_weight: 该来源的固定权重。
        decision: 四操作决策。
        trace_id: 链路追踪标识（审计可答「谁在何时写入什么」）。
        created_at: 事件时刻（UTC ISO 字符串）。
        superseded_at: 被新值取代的时刻（不删行，仅失效）。
        revoked_at: 被用户清除或采纳证据撤销的时刻。
        row_hash: hash 链当前行哈希（防篡改，与 guard_hit 同机制）。
    """

    user_id: str
    session_id: str | None
    field_name: PreferenceField
    old_value: JsonValue | None
    new_value: JsonValue
    source: PreferenceSource
    source_weight: float
    decision: PreferenceDecision
    trace_id: str
    created_at: str
    superseded_at: str | None = None
    revoked_at: str | None = None
    row_hash: str = ""


@dataclass(frozen=True)
class UserProcedure:
    """程序记忆：用户采纳某 recipe/widget 结构的计数（D-14，零 LLM）。

    Attributes:
        user_id: 租户隔离键。
        recipe_id: 被采纳的规程标识（规程正文由 M10 维护，此处只存句柄）。
        widget_type: 采纳控件的类型。
        adopt_count: 累计显式采纳次数（删除/回滚递减）。
        last_adopted_at: 最近采纳时刻（UTC ISO 字符串）。
        version: 版本号，任一有效采纳/撤销 ``+1``。
    """

    user_id: str
    recipe_id: str
    widget_type: str
    adopt_count: int
    last_adopted_at: str
    version: int = PREFERENCE_VERSION_INITIAL


def utc_now_iso() -> str:
    """返回当前 UTC 时刻的 ISO 字符串（秒级，供证据时间戳使用）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")
