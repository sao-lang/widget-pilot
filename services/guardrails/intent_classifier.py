"""L3 / L4 分类器默认实现（§4.3 / §6.2）：零依赖兜底，真实端点经 M20/M07 注入。"""

from __future__ import annotations

from services.guardrails.constants import MAX_CLARIFY_TOKENS
from services.guardrails.types import IntentCategory, LowTierResult


class RuleBasedClassifier:
    """默认实现：返回「不可解析」，触发 L3→L4 升级路径（结构化判据）。

    这是 fail-closed 的零依赖实现——不凭空裁定意图，也不跳过判定放行。
    """

    def classify(self, text: str, max_tokens: int) -> LowTierResult:
        """始终返回不可解析，交由级联按结构化判据升级。"""
        return LowTierResult(intent=None, parseable=False, tokens_used=0)


class StructuredFallback:
    """L4 默认兜底：不调用模型，按白名单方向结构化归类为控件生成。

    这是 fail-safe 的零依赖实现——L1 已排除拒绝类，L4 只做放行方向归类，
    禁止把 L4 用作安全裁判（§3.1）。真实主 LLM 由 M07 ModelGateway 注入。
    """

    def resolve(self, text: str, max_tokens: int) -> LowTierResult:
        """结构化兜底：放行方向的控件生成意图，不产生拒绝类。"""
        return LowTierResult(
            intent=IntentCategory.WIDGET_GENERATION,
            parseable=True,
            tokens_used=0,
        )


def default_classifier() -> RuleBasedClassifier:
    """构造默认 L3 分类器（真实低档端点由部署注入替换）。"""
    return RuleBasedClassifier()


def default_gateway() -> StructuredFallback:
    """构造默认 L4 兜底（真实主 LLM 由 M07 注入替换）。"""
    return StructuredFallback()


DEFAULT_MAX_TOKENS = MAX_CLARIFY_TOKENS
