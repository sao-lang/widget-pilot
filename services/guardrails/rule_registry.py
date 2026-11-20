"""自研 pattern / rule registry（§3.4 B）：启动期静态注册，运行期只读。

扫描是纯函数：首条命中即返回，留 hit_type / rule_id / judgement / 原文引用（截断）。
PII 规则的 excerpt 做掩码，不存未脱敏原文（§6.5 红线）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.guardrails.constants import EXCERPT_MAX_CHARS
from services.guardrails.patterns import injection, intent, pii
from services.guardrails.types import GuardHitType, InputGuardHit, PatternRule


@dataclass(slots=True)
class RuleRegistry:
    """规则注册表（运行时不可增删，构造后视为只读）。

    Attributes:
        rules: 已注册的有序规则表；扫描按此顺序，首条命中即返回。
    """

    rules: tuple[PatternRule, ...] = ()

    def register(self, rule: PatternRule) -> RuleRegistry:
        """启动期追加一条规则，返回新注册表（保持既有对象只读语义）。"""
        return RuleRegistry(rules=(*self.rules, rule))


def default_registry() -> RuleRegistry:
    """构造内置规则表：注入 → PII → 意图，顺序固定（§3.4 B）。"""
    rules = (*injection.RULES, *pii.RULES, *intent.RULES)
    return RuleRegistry(rules=rules)


def _mask(excerpt: str, hit_type: GuardHitType, rule_id: str = "") -> str:
    """对命中原文截断；PII 类型按规则分别掩码，不留未脱敏信息（§2.3/§6.5）。

    邮箱无连续 4 位数字，只掩码数字会原样留存地址，故按 rule_id 分治。
    """
    clipped = excerpt[:EXCERPT_MAX_CHARS]
    if hit_type is GuardHitType.PII:
        if rule_id == "PII_EMAIL":
            # 邮箱：保留域名判据可读性，掩码本地部分
            local, sep, domain = clipped.partition("@")
            return f"{local[:1]}***@{domain}" if sep else "***"
        # 手机号 / 身份证号：所有数字掩码，保留分隔符便于溯源
        return re.sub(r"\d", "*", clipped)
    return clipped


def scan(text: str, registry: RuleRegistry) -> tuple[InputGuardHit, ...]:
    """按注册顺序扫描文本，首条命中即返回。

    Args:
        text: 待扫描文本（描述 + 附件 extracted 的拼接）。
        registry: 静态规则表。

    Returns:
        至多一条 InputGuardHit 的元组；无命中返回空元组。
    """
    for rule in registry.rules:
        if not rule.enabled:
            continue
        match = re.search(rule.regex, text, flags=re.IGNORECASE)
        if match:
            return (
                InputGuardHit(
                    hit_type=rule.hit_type,
                    rule_id=rule.rule_id,
                    judgement=rule.judgement,
                    excerpt=_mask(match.group(0), rule.hit_type, rule.rule_id),
                ),
            )
    return ()
