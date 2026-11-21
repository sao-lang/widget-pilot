"""意图四级级联装配（ADR-001 D-8）：L1 规则 → L2 语义路由 → L3 小模型 → L4 兜底。

【本模块职责】级联判定、升级判据、留证、默认零依赖实现、注入端口契约。
【其他模块职责】L2 embedding（M20）/ L3 低档端点（M20/M07）/ L4 主 LLM（M07）
按 ``ports.py`` 的 Protocol 注入；未注入走默认实现（不静默跳过）。

升级只由结构化判据触发（词表未命中 / 相似度 < 阈值 / 输出不可解析 / 端点异常），
拒绝类（OUT_OF_SCOPE）恒出自 L1，绝不进任何模型（I14）。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import cast

from services.guardrails.constants import (
    DEV_CONTEXT_PATTERN,
    MAX_CLARIFY_TOKENS,
    OUT_OF_SCOPE_PATTERNS,
    WIDGET_GENERATION_PATTERNS,
)
from services.guardrails.intent_classifier import (
    RuleBasedClassifier,
    StructuredFallback,
)
from services.guardrails.ports import Embedder, IntentClassifier, IntentGateway
from services.guardrails.semantic_router import SemanticRouter
from services.guardrails.types import (
    IntentCategory,
    IntentDecision,
    LowTierResult,
    ModelTier,
    RouteLayer,
    RouteResult,
)

logger = logging.getLogger(__name__)

# 降级归因常量（留证用，禁魔法字符串）
_FB_L2_UNAVAILABLE = "L2_EMBEDDER_UNAVAILABLE"
_FB_L3_UNAVAILABLE = "L3_CLASSIFIER_UNAVAILABLE"
_FB_L3_UNPARSEABLE = "L3_OUTPUT_UNPARSEABLE"
_FB_L4_UNAVAILABLE = "L4_GATEWAY_UNAVAILABLE"
_FB_L4_OUT_OF_SCOPE = (
    "L4_OUTPUT_OUT_OF_SCOPE"  # L4 违规输出拒绝类（不采纳，但留证告警）
)
_FB_L4_UNPARSEABLE = "L4_OUTPUT_UNPARSEABLE"  # L4 输出不可解析


def _l1_rule_intent(text: str) -> IntentCategory | None:
    """L1 词表：控件生成正向命中优先，其次越界词表（I14 拒绝类仍出自 L1）。

    正向优先的原因：越界词表含「天气 / 聊天 / 翻译」等高频裸词，而
    「做一个天气预报的日历 widget」「生成一个翻译任务清单卡片」都是合法控件请求。
    若拒绝类无条件优先，这些请求会被一刀切拒绝，违背「只放行控件生成」的产品定位。
    真正的攻击面（注入 / PII）由 input_guard 独立扫描，不受此处优先级影响。
    """
    # 开发语境是整句拒绝类（N6），须先于正向：ADR-004 复审 S2
    # 「用 Swift 做个小组件项目」含生成动词，但不生成手机 widget，而是写代码。
    if re.search(DEV_CONTEXT_PATTERN, text, flags=re.IGNORECASE):
        return IntentCategory.OUT_OF_SCOPE
    # 正向：明确的控件生成意图（动作 × 宿主/控件）优先放行
    for pattern in WIDGET_GENERATION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return IntentCategory.WIDGET_GENERATION
    # 越界：无控件意图时，开放域词表命中才拒
    for pattern in OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return IntentCategory.OUT_OF_SCOPE
    return None


def is_out_of_scope(text: str) -> bool:
    """L1 越界判定，供装配层在语义层之前短路（A3/A24，I14）。

    越界是「拒绝类」，必须先于描述充分性 / 规格询问判定——否则短描述或
    无 grid 的越界请求会先拿到 INPUT_INVALID / SPEC_MISSING，A3 永不兑现。
    此函数零模型调用。
    """
    return _l1_rule_intent(text) is IntentCategory.OUT_OF_SCOPE


def _try_route(
    text: str,
    embedder: Embedder | None,
    threshold: float | None,
) -> tuple[RouteResult | None, str | None]:
    """L2 语义路由；端点异常返回 (None, 归因)，供级联按结构化判据降级。

    router 构造期会预嵌入样本——若注入的 embedder 不可用，异常在此被吞并降级，
    不向上传播，也不跳过判定。
    """
    try:
        router = (
            SemanticRouter(embedder=embedder, threshold=threshold)
            if threshold is not None
            else SemanticRouter(embedder=embedder)
        )
        return router.route(text), None
    except Exception:  # noqa: BLE001 - 端点不可达一律降级，不跳过判定
        logger.warning("L2 embedder 不可用，降级 L3（text=%.50s）", text)
        return None, _FB_L2_UNAVAILABLE


def _try_classify(
    text: str,
    classifier: IntentClassifier,
) -> tuple[LowTierResult | None, str | None]:
    """L3 小模型分类；端点异常或输出不可解析都给归因，供升级 L4。"""
    try:
        result = classifier.classify(text, MAX_CLARIFY_TOKENS)
    except Exception:  # noqa: BLE001 - 端点不可达一律升级，不跳过判定
        logger.warning("L3 classifier 不可用，升级 L4（text=%.50s）", text)
        return None, _FB_L3_UNAVAILABLE
    if (
        not result.parseable
        or result.intent is None
        or result.intent is IntentCategory.OUT_OF_SCOPE
    ):
        return None, _FB_L3_UNPARSEABLE
    return result, None


def classify_intent(
    text: str,
    embedder: Embedder | None = None,
    classifier: IntentClassifier | None = None,
    gateway: IntentGateway | None = None,
    threshold: float | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> IntentDecision:
    """级联裁定意图，记录命中层 / 相似度 / 耗时 / 降级归因（§3.2 step11）。

    Args:
        text: 待裁定描述。
        embedder: L2 向量化端口（M20 注入；默认纯代码）。
        classifier: L3 分类端口（M20/M07 注入；默认不可解析触发升级）。
        gateway: L4 主 LLM 端口（M07 注入；默认结构化兜底）。
        threshold: L2 阈值覆盖（默认版本化阈值）。
        on_fallback: 降级告警回调（部署可接 M11 告警；默认仅日志）。

    Returns:
        IntentDecision；resolved=True 表示已裁定（含 L1 拒绝）。
    """
    started = time.monotonic()
    clf: IntentClassifier = classifier or RuleBasedClassifier()
    gw: IntentGateway = gateway or StructuredFallback()

    def alert(reason: str) -> None:
        """统一降级告警出口（不静默跳过，§3.1）。"""
        if on_fallback is not None:
            on_fallback(reason)

    # L1：拒绝类恒在此拦截（I14），不触任何模型——router 不构造，
    # 否则构造期预嵌入样本会调用 embedder，破坏「拒绝类 embed 调用恒 0」。
    l1 = _l1_rule_intent(text)
    if l1 is not None:
        return IntentDecision(
            intent=l1,
            route_layer=RouteLayer.L1,
            resolved=True,
            latency_ms=_elapsed_ms(started),
        )

    # L2：语义路由；阈值命中即裁定，不触 L3/L4
    route_obj, l2_fb = _try_route(text, embedder, threshold)
    if l2_fb is not None:
        alert(l2_fb)
    elif (
        route_obj is not None
        and route_obj.threshold_hit
        and route_obj.intent is not None
        and route_obj.intent
        is not IntentCategory.OUT_OF_SCOPE  # 拒绝类只出自 L1（I14）
    ):
        return IntentDecision(
            intent=route_obj.intent,
            route_layer=RouteLayer.L2,
            resolved=True,
            similarity=route_obj.similarity,
            latency_ms=_elapsed_ms(started),
            threshold_hit=True,
        )

    # L3：低档小模型分类；端点不可用 / 输出不可解析都升级 L4
    l3_result, l3_fb = _try_classify(text, clf)
    if l3_fb is not None:
        alert(l3_fb)
        reason = l3_fb
    else:
        reason = None
    if l3_result is not None:
        return IntentDecision(
            # _try_classify 已排除 intent=None / OUT_OF_SCOPE，此处收口为非空类型
            intent=cast(IntentCategory, l3_result.intent),
            route_layer=RouteLayer.L3,
            resolved=True,
            similarity=route_obj.similarity if route_obj is not None else None,
            latency_ms=_elapsed_ms(started),
            model_tier=ModelTier.CHEAP,
            fallback_reason=l2_fb,  # L2 曾降级则在此归因
        )

    # L4：主 LLM 兜底（注入端口）；异常则回落到结构化兜底，绝不跳过判定放行
    l4_fb: str | None = None
    try:
        l4_result = gw.resolve(text, MAX_CLARIFY_TOKENS)
    except Exception:  # noqa: BLE001 - 端点异常回落结构化兜底，不跳过判定
        logger.warning("L4 gateway 不可用，回落结构化兜底（text=%.50s）", text)
        alert(_FB_L4_UNAVAILABLE)
        l4_fb = _FB_L4_UNAVAILABLE
        l4_result = None
    if (
        l4_result is not None
        and l4_result.parseable
        and l4_result.intent is not None
        and l4_result.intent is not IntentCategory.OUT_OF_SCOPE
    ):
        final_intent = l4_result.intent
    else:
        # L4 输出违规（拒绝类 / 不可解析）也必须留证告警，不能静默回落
        if l4_fb is None:
            l4_fb = (
                _FB_L4_OUT_OF_SCOPE
                if l4_result is not None
                and l4_result.intent is IntentCategory.OUT_OF_SCOPE
                else _FB_L4_UNPARSEABLE
            )
            alert(l4_fb)
        # 结构化兜底：白名单方向归类为控件生成（L1 已排除拒绝类）
        final_intent = IntentCategory.WIDGET_GENERATION

    # 归因优先级：L4 自身降级 > L3 > L2（最深 failure 是最终兜底的决定因素）
    final_reason = (
        l4_fb if l4_fb is not None else (reason if reason is not None else l2_fb)
    )
    return IntentDecision(
        intent=final_intent,
        route_layer=RouteLayer.L4,
        resolved=True,
        similarity=route_obj.similarity if route_obj is not None else None,
        latency_ms=_elapsed_ms(started),
        model_tier=ModelTier.FRONTIER,
        fallback_reason=final_reason,
    )


def _elapsed_ms(started: float) -> int:
    """把单调时钟起点换算为整数毫秒耗时（留证用）。"""
    return int((time.monotonic() - started) * 1000)
