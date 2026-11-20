"""PII 模式库（§3.4 B）：手机号 / 邮箱 / 身份证。"""

from __future__ import annotations

from services.guardrails.types import GuardHitType, PatternRule

RULES: tuple[PatternRule, ...] = (
    PatternRule(
        rule_id="PII_PHONE_CN",
        hit_type=GuardHitType.PII,
        # 中文在 re 中属 \w，\b 在「手机号是138…」处不成立；改用数字前后瞻
        regex=r"(?<!\d)1[3-9]\d{9}(?!\d)",
        judgement="大陆手机号：拒绝并掩码，原文引用不存未脱敏号码",
    ),
    PatternRule(
        rule_id="PII_EMAIL",
        hit_type=GuardHitType.PII,
        regex=r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
        judgement="邮箱地址：拒绝并掩码",
    ),
    PatternRule(
        rule_id="PII_ID_CN",
        hit_type=GuardHitType.PII,
        regex=r"(?<![\dXx])\d{17}[\dXx](?![\dXx])",
        judgement="大陆身份证号：拒绝并掩码",
    ),
)
