"""M03 出 LLM 护栏（§11.8 / §4.1 L25）：schema / 能力 / 禁止内容 / PII / 规格。

纯代码规则，命中写 guard_hit，误拦截率可测。
"""
from __future__ import annotations

import re

from packages.agent_core.guardrails.input_guard import RuleSet
from packages.agent_core.types import GuardVerdict, ModelOutput


def output_guard(output: ModelOutput, rules: RuleSet) -> GuardVerdict:
    text = output.content or ""
    # 禁止内容：eval / 未知 import（非 react / @widget/bridge）
    if re.search(r"\beval\s*\(|new\s+Function\s*\(", text, re.IGNORECASE):
        return GuardVerdict(hit=True, guard="output_guard", hit_type="forbidden_eval",
                            criterion="OUT-001", source_ref="eval(", reason_code="GUARD_OUTPUT_HIT")
    if re.search(r"fetch\s*\(|XMLHttpRequest|WebSocket\s*\(", text, re.IGNORECASE):
        return GuardVerdict(hit=True, guard="output_guard", hit_type="forbidden_network",
                            criterion="OUT-002", source_ref="fetch(", reason_code="GUARD_OUTPUT_HIT")
    for m in re.finditer(r"(?:from|require\s*\()\s*['\"]([^'\"]+)['\"]", text):
        mod = m.group(1)
        if mod not in ("react", "@widget/bridge"):
            return GuardVerdict(hit=True, guard="output_guard", hit_type="unknown_import",
                                criterion="OUT-003", source_ref=mod, reason_code="GUARD_OUTPUT_HIT")
    # PII
    for rule in rules.rules:
        if rule.hit_type == "pii":
            m = re.search(rule.pattern, text, re.IGNORECASE)
            if m:
                return GuardVerdict(hit=True, guard="output_guard", hit_type="pii",
                                    criterion=rule.rule_id, source_ref=m.group(0),
                                    reason_code="GUARD_OUTPUT_HIT")
    return GuardVerdict(hit=False, guard="output_guard")
