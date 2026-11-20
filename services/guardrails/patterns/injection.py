"""注入模式库（§3.4 B）：每条规则带 rule_id + 正则 + 判据。"""

from __future__ import annotations

from services.guardrails.types import GuardHitType, PatternRule

RULES: tuple[PatternRule, ...] = (
    PatternRule(
        rule_id="INJ_IGNORE_RULES",
        hit_type=GuardHitType.INJECTION,
        regex=r"忽略.{0,8}(之前|上面|以上|所有).{0,6}(规则|指令|提示)|ignore\s+(all\s+)?previous\s+instructions",
        judgement="疑似提示注入：试图让模型忽略既有规则",
    ),
    PatternRule(
        rule_id="INJ_SYSTEM_PROMPT",
        hit_type=GuardHitType.INJECTION,
        regex=r"(输出|显示|打印|reveal|print).{0,10}(系统提示|system\s+prompt)",
        judgement="疑似提示泄露：试图套取系统提示",
    ),
    PatternRule(
        rule_id="INJ_EXTERNAL_CALL",
        hit_type=GuardHitType.INJECTION,
        regex=r"fetch\s*\(|XMLHttpRequest|外联|外部请求",
        judgement="疑似外联注入：试图生成对外网络请求代码",
    ),
)
