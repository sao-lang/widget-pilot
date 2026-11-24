"""M06 共享数据类型（§6.2 内部协议，v1.5 统一命名）。

集中定义 M06 各模块间传递的数据结构与枚举，避免各文件重复定义导致漂移。
命名与 M03 `packages/agent_core/types.py` 区分：本模块命中记录叫 `InputGuardHit`，
M03 库级护栏记录叫 `GuardHit`，两者不同层、不同词表，禁止混用（§6.2 v1.5 澄清）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from packages.contracts.enums import GridSpec, ModelTier, ReasonCode, WidgetType
from packages.contracts.types import JsonObject

# 规格规则表四元组：(grid, 特征词, 判定说明, 建议规格)，冲突矩阵与密度提示共用
RuleTable = tuple[tuple[GridSpec, str, str, GridSpec], ...]


class GuardHitType(StrEnum):
    """进护栏命中类型（§5.1，大写枚举，与 M03 小写 _HitType 区分）。"""

    INJECTION = "INJECTION"
    PII = "PII"
    SENSITIVE = "SENSITIVE"
    INTENT = "INTENT"
    BUDGET = "BUDGET"


class HitDecision(StrEnum):
    """命中处置决策（§6.1 decision）。"""

    REJECT = "REJECT"
    MASK = "MASK"
    AWAIT = "AWAIT"


class IntentCategory(StrEnum):
    """意图分类（§5.1 / ADR-001 D-8，封闭集合）。"""

    WIDGET_GENERATION = "WIDGET_GENERATION"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class RouteLayer(StrEnum):
    """级联命中层（§5.1）。"""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class GridSource(StrEnum):
    """规格来源溯源（§5.1；未定格时为 None，不新增 missing 值）。"""

    SELECTOR = "selector"
    EXPLICIT_TEXT = "explicit_text"
    CLARIFIED = "clarified"


class GridStatus(StrEnum):
    """GridResolver 本次解析路径（§4.3 status，不取 clarified）。"""

    SELECTOR = "selector"
    EXPLICIT_TEXT = "explicit_text"
    MISSING = "missing"


@dataclass(slots=True, frozen=True)
class InputGuardHit:
    """进护栏一次命中留证（§6.2 InputGuardHit，落 guard_hit 表）。"""

    hit_type: GuardHitType
    rule_id: str
    judgement: str
    excerpt: str
    decision: HitDecision = HitDecision.REJECT


@dataclass(slots=True, frozen=True)
class GuardResult:
    """check_input 的判定结果（§4.3 GuardResult）。"""

    passed: bool
    hits: tuple[InputGuardHit, ...] = ()
    short_circuit: bool = False
    masked_text: str | None = None


@dataclass(slots=True, frozen=True)
class PatternRule:
    """规则注册表条目（§6.1 RuleRegistryEntry，启动期静态注册）。"""

    rule_id: str
    hit_type: GuardHitType
    regex: str
    judgement: str
    version: str = "v1"
    enabled: bool = True


@dataclass(slots=True, frozen=True)
class AttachmentView:
    """M05 附件视图（§1.4 入，role 与 role_confidence 由 M05 判定）。

    Attributes:
        attachment_id: 附件标识（M05 产出）。
        role: M05 判定的用途角色；None 表示歧义（由 M06 转 AWAITING_INPUT）。
        role_confidence: role 判定置信度（v1.7 S1 正名，非旧 confidence）；
            M06 R2 据此与阈值 0.6 比较判明确性，绝不用 insight_quality 代替。
        insight_ref: 附件洞察对象引用（含 insight_quality，属处理质量非 role 明确性）。
        extracted_text: 提取文本（不可信数据扫描用，不进快照原文）。
    """

    attachment_id: str
    role: str | None
    role_confidence: float
    insight_ref: str | None
    extracted_text: str = ""


@dataclass(slots=True, frozen=True)
class BudgetView:
    """M11 预算余量视图（§1.4 入）。"""

    left: float
    user_id: str = ""


@dataclass(slots=True, frozen=True)
class SpecConflict:
    """规格冲突提示（§6.2 notice.spec_conflict）。"""

    grid: GridSpec
    feature: str
    suggest_grid: GridSpec


@dataclass(slots=True, frozen=True)
class SpecMissing:
    """规格缺失询问建议（§6.2 notice.spec_missing）。"""

    suggest_grid: tuple[GridSpec, ...]


@dataclass(slots=True, frozen=True)
class SemanticResult:
    """语义层校验结果（§4.3 SemanticResult）。"""

    ok: bool
    reason_code: ReasonCode | None = None
    suggestions: tuple[str, ...] = ()
    spec_conflict: SpecConflict | None = None
    spec_missing: SpecMissing | None = None
    grid: GridSpec | None = None
    grid_source: GridSource | None = None


@dataclass(slots=True, frozen=True)
class GridResolution:
    """规格解析结果（§4.3 GridResolution，v1.5 明确 status/grid_source 映射）。"""

    status: GridStatus
    grid: GridSpec | None
    grid_source: GridSource | None
    suggest_grid: tuple[GridSpec, ...] = ()


@dataclass(slots=True, frozen=True)
class Embedding:
    """向量（§4.3 L2）；以 tuple 承载，纯 Python 零依赖。"""

    values: tuple[float, ...]


@dataclass(slots=True, frozen=True)
class RouteResult:
    """L2 语义路由结果（§4.3 RouteResult）。"""

    intent: IntentCategory | None
    similarity: float
    threshold_hit: bool


@dataclass(slots=True, frozen=True)
class LowTierResult:
    """L3 小模型分类结果（§4.3 classify_low_tier）。"""

    intent: IntentCategory | None
    parseable: bool
    tokens_used: int = 0


@dataclass(slots=True, frozen=True)
class IntentDecision:
    """级联裁定结果（§4.3 IntentDecision）。"""

    intent: IntentCategory
    route_layer: RouteLayer
    resolved: bool
    similarity: float | None = None
    latency_ms: int = 0
    model_tier: ModelTier | None = None
    threshold_hit: bool = (
        False  # L2 阈值是否命中（§4.2 routing.threshold_hit，非「有相似度」）
    )
    fallback_reason: str | None = None  # 降级归因（L2/L3 端点不可用或不可解析）


@dataclass(slots=True, frozen=True)
class CapabilityRequest:
    """tool/skill 通用的能力加载请求（ADR-003 / FR-6）。

    Attributes:
        capability_id: 能力句柄（tool.* / recipe.*）。
        level: 披露层级；1=详情，2=执行/正文。
        surface: 调用表面（generation / widget-runtime），确定性授权用。
        reason: 模型给出的加载理由（仅留证，不作判据）。
    """

    capability_id: str
    level: int
    surface: str = "generation"
    reason: str = ""


@dataclass(slots=True, frozen=True)
class CapabilityCheckResult:
    """能力请求校验结果（ADR-003 / FR-6）。"""

    ok: bool
    decision: HitDecision | None = None
    reason: str | None = None


@dataclass(slots=True)
class TurnState:
    """单轮能力加载计数状态（ADR-003 上限），可变计数由装配层持有。"""

    capability_count: int = 0
    capability_tokens: int = 0


@dataclass(slots=True, frozen=True)
class ValidationResult:
    """校验链装配输出（§6.2，v1.5 统一三处定义）。"""

    ok: bool
    reason_code: ReasonCode | None = None
    widget_type: WidgetType | None = None
    grid: GridSpec | None = None
    grid_source: GridSource | None = None
    model_tier: ModelTier | None = None
    intent_normalized: IntentCategory | None = None
    attachment_refs: tuple[str, ...] = ()
    route_layer: RouteLayer | None = None
    similarity: float | None = None
    latency_ms: int = 0
    trace_id: str = ""
    evidence_ref: str | None = (
        None  # 输入快照证据引用（§4.2 error/面包屑，指向带 hash 的快照）
    )


@dataclass(slots=True, frozen=True)
class EventPayload:
    """M06 产生的 SSE 事件 payload（§4.2）。"""

    kind: str
    payload: JsonObject = field(default_factory=dict)
