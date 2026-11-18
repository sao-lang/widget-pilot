"""M03 进 LLM 护栏（§11.8 / §4.1 L24）：纯代码规则 + 模式库。

命中写 guard_hit（类型 / 判据 / 原文引用），判据为确定性代码，误拦截率可测。
不做：不靠 prompt 声明「描述是数据」来防注入（§7.2）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.agent_core.types import GuardVerdict, _GuardName, _HitType
from packages.contracts.enums import ReasonCode


@dataclass
class Rule:
    """一条进向护栏规则：把正则命中映射到判据 ID 与 ReasonCode。"""

    rule_id: str
    hit_type: str
    pattern: str          # 正则（大小写不敏感）
    reason_code: str


@dataclass
class RuleSet:
    """规则集合，对外提供开箱即用的默认规则集。"""

    rules: list[Rule] = field(default_factory=list)

    @classmethod
    def default(cls) -> RuleSet:
        """构造内置规则集，覆盖提示注入、提示泄露、PII 与越界调用四类风险。"""
        return cls(rules=[
            Rule("INJ-001", _HitType.PROMPT_INJECTION, r"忽略.{0,6}(之前|上面|所有).{0,6}(规则|指令|提示)|ignore\s+(all\s+)?previous\s+instructions", ReasonCode.GUARD_INPUT_HIT),
            Rule("INJ-002", _HitType.PROMPT_LEAK, r"(输出|显示|reveal|print).{0,10}(系统提示|system\s+prompt)", ReasonCode.GUARD_INPUT_HIT),
            Rule("PII-001", _HitType.PII, r"\b\d{17}[\dXx]\b|\b1[3-9]\d{9}\b", ReasonCode.GUARD_INPUT_HIT),
            Rule("INT-001", _HitType.OUT_OF_SCOPE, r"fetch\s*\(|外联|\beval\s*\(|访问.{0,4}系统\s*API", ReasonCode.GUARD_INPUT_HIT),
        ])


def input_guard(text: str, rules: RuleSet) -> GuardVerdict:
    """逐条规则匹配；命中即返回（第一条命中，留证 criterion + source_ref）。"""
    import re
    # 按注册顺序逐条匹配，保证同一文本的首条命中结果确定、可复现
    for rule in rules.rules:
        m = re.search(rule.pattern, text, flags=re.IGNORECASE)
        # 命中即留证并返回，后续规则不再检查（首条命中语义）
        if m:
            return GuardVerdict(
                hit=True,
                guard=_GuardName.INPUT,
                hit_type=rule.hit_type,
                criterion=rule.rule_id,
                source_ref=m.group(0),          # 原文引用（命中片段）
                reason_code=rule.reason_code,
            )
    return GuardVerdict(hit=False, guard=_GuardName.INPUT)
