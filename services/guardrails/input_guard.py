"""进 LLM 护栏 L1（§3.2 step7-10）：注入/PII → 敏感词 → 意图 → 预算。

四类判据各自独立、命中不进任何模型（I1/I14）。注入/PII 走确定性规则表 +
成熟库真值校验；敏感词走 Aho-Corasick 分类词库（涉政词库外置）；意图是上游
级联给出的分类，越界意图（OUT_OF_SCOPE）在 L1 落 INTENT 命中。
"""

from __future__ import annotations

from services.guardrails.constants import INTENT_ALLOWLIST
from services.guardrails.dangerous_call import detect_dangerous_calls
from services.guardrails.rule_registry import default_registry, scan
from services.guardrails.sensitive import (
    SensitiveWordDetector,
    default_sensitive_detector,
)
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
    sensitive_detector: SensitiveWordDetector | None = None,
) -> GuardResult:
    """对描述与附件提取文本执行五类 L1 判定。

    Args:
        description: 用户描述。
        extracted_texts: 附件 / 检索片段的提取文本（按不可信数据扫描）。
        intent: 上游 L1 意图词表给出的意图分类。
        budget: M11 预算余量视图。
        sensitive_detector: 敏感词检测器；None 用进程级默认实现（内置词库 + 外置合规词库）。

    Returns:
        GuardResult；任一命中即 passed=False 且 short_circuit=True。
    """
    # 扫描对象 = 描述 + 附件提取文本（§6.5 只扫 extracted，不碰附件原文）
    joined = "\n".join([description, *extracted_texts])
    rule_hits = scan(joined, default_registry())
    if rule_hits:
        return GuardResult(passed=False, hits=rule_hits, short_circuit=True)

    detector = sensitive_detector or default_sensitive_detector()
    sensitive = detector.scan(joined)
    if sensitive is not None:
        return GuardResult(
            passed=False,
            hits=(detector.to_guard_hit(sensitive),),
            short_circuit=True,
        )

    # ADR-010 §3：危险接口 / 高危设备 API / 危险动作（词表 + 形状层，零模型）。
    # 命中按 INJECTION 类型留证（未授权调用指令语义），不新增 ReasonCode。
    danger = detect_dangerous_calls(description, extracted_texts=tuple(extracted_texts))
    if danger.hit:
        hit = InputGuardHit(
            hit_type=GuardHitType.INJECTION,
            rule_id="DANGEROUS_CALL",
            judgement="危险接口/高危设备 API/危险动作：" + ";".join(danger.violations),
            excerpt=description[:80],
            decision=HitDecision.REJECT,
        )
        return GuardResult(passed=False, hits=(hit,), short_circuit=True)

    # 注入/PII/敏感词/危险调用未命中，再判意图白名单（只放行控件生成）
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
