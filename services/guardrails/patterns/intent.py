"""意图 L1 词表（§3.4 B）：明确非控件特征的硬规则。"""

from __future__ import annotations

from services.guardrails.types import GuardHitType, PatternRule

RULES: tuple[PatternRule, ...] = (
    PatternRule(
        rule_id="INTENT_GENERIC_CHAT",
        hit_type=GuardHitType.INTENT,
        regex=r"今天(天气|新闻).*怎么样|讲个笑话|写一首诗|翻译这段|讲讲历史",
        judgement="开放域聊天 / 非控件生成请求：INTENT_OUT_OF_SCOPE",
    ),
)
