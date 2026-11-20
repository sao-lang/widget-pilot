"""进 LLM 护栏 L1（§3.2 step7-10）：注入 → PII → 意图 → 预算，首类命中即短路。

四类判据各自独立、命中不进任何模型（I1/I14）。这里的意图是上游级联给出的分类，
越界意图（OUT_OF_SCOPE）在 L1 落 INTENT 命中；文本注入/PII 仍由规则表扫描。
"""

from __future__ import annotations

from services.guardrails.constants import INTENT_ALLOWLIST
from services.guardrails.rule_registry import default_registry, scan
from services.guardrails.types import (
    BudgetView,
    GuardHitType,
    GuardResult,
    HitDecision,
    InputGuardHit,
)

_OUT_OF_SCOPE_RULE_ID = "INTENT_OUT_OF_SCOPE"
_BUDGET_RULE_ID = "BUDGET_EXHAUSTED"


def check_input(
    description: str,
    extracted_texts: list[str],
    intent: str,
    budget: BudgetView,
) -> GuardResult:
    """对描述与附件提取文本执行四类 L1 判定。

    Args:
        description: 用户描述。
        extracted_texts: 附件 / 检索片段的提取文本（按不可信数据扫描）。
        intent: 上游 L1 意图词表给出的意图分类。
        budget: M11 预算余量视图。

    Returns:
        GuardResult；任一命中即 passed=False 且 short_circuit=True。
    """
    # 扫描对象 = 描述 + 附件提取文本（§6.5 只扫 extracted，不碰附件原文）
    joined = "\n".join([description, *extracted_texts])
    rule_hits = scan(joined, default_registry())
    if rule_hits:
        return GuardResult(passed=False, hits=rule_hits, short_circuit=True)

    # 注入/PII 文本规则未命中，再判意图白名单（只放行控件生成）
    if intent not in INTENT_ALLOWLIST:
        hit = InputGuardHit(
            hit_type=GuardHitType.INTENT,
            rule_id=_OUT_OF_SCOPE_RULE_ID,
            judgement=f"意图 {intent} 不在白名单，仅放行控件生成",
            excerpt=description[:80],
            decision=HitDecision.REJECT,
        )
        return GuardResult(passed=False, hits=(hit,), short_circuit=True)

    # 预算前置：余量 ≤ 0 确定性中断（不进模型、不自动重试）
    if budget.left <= 0:
        hit = InputGuardHit(
            hit_type=GuardHitType.BUDGET,
            rule_id=_BUDGET_RULE_ID,
            judgement="预算余量触顶，提交时确定性中断",
            excerpt="",
            decision=HitDecision.REJECT,
        )
        return GuardResult(passed=False, hits=(hit,), short_circuit=True)

    return GuardResult(passed=True)
