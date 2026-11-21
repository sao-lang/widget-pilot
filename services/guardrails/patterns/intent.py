"""意图 L1 词表（§3.4 B）：明确非控件特征的硬规则。"""

from __future__ import annotations

from services.guardrails.constants import INTENT_GUARD_PATTERNS
from services.guardrails.types import GuardHitType, PatternRule

RULES: tuple[PatternRule, ...] = (
    PatternRule(
        rule_id="INTENT_GENERIC_CHAT",
        hit_type=GuardHitType.INTENT,
        # ADR-004：与 L1 负向语义族同源的 check_input 子集
        # （N1/N4 加宿主+动作联合排除，因进护栏没有正向优先）
        regex="(?:" + "|".join(INTENT_GUARD_PATTERNS) + ")",
        judgement="开放域聊天 / 非控件生成请求：INTENT_OUT_OF_SCOPE",
    ),
)
