"""M03 进 LLM 护栏（§11.8 / §4.1 L24）：纯代码规则 + 模式库。

命中写 guard_hit（类型 / 判据 / 原文引用），判据为确定性代码，误拦截率可测。
不做：不靠 prompt 声明「描述是数据」来防注入（§7.2）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.agent_core.types import GuardVerdict


@dataclass
class Rule:
    rule_id: str
    hit_type: str
    pattern: str          # 正则（大小写不敏感）
    reason_code: str


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)

    @classmethod
    def default(cls) -> RuleSet:
        return cls(rules=[
            Rule("INJ-001", "prompt_injection", r"忽略.{0,6}(之前|上面|所有).{0,6}(规则|指令|提示)|ignore\s+(all\s+)?previous\s+instructions", "GUARD_INPUT_HIT"),
            Rule("INJ-002", "prompt_leak", r"(输出|显示|reveal|print).{0,10}(系统提示|system\s+prompt)", "GUARD_INPUT_HIT"),
            Rule("PII-001", "pii", r"\b\d{17}[\dXx]\b|\b1[3-9]\d{9}\b", "GUARD_INPUT_HIT"),
            Rule("INT-001", "out_of_scope", r"fetch\s*\(|外联|\beval\s*\(|访问.{0,4}系统\s*API", "GUARD_INPUT_HIT"),
        ])


def input_guard(text: str, rules: RuleSet) -> GuardVerdict:
    """逐条规则匹配；命中即返回（第一条命中，留证 criterion + source_ref）。"""
    import re
    for rule in rules.rules:
        m = re.search(rule.pattern, text, flags=re.IGNORECASE)
        if m:
            return GuardVerdict(
                hit=True,
                guard="input_guard",
                hit_type=rule.hit_type,
                criterion=rule.rule_id,
                source_ref=m.group(0),          # 原文引用（命中片段）
                reason_code=rule.reason_code,
            )
    return GuardVerdict(hit=False, guard="input_guard")
