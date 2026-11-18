"""M03 出 LLM 护栏（§11.8 / §4.1 L25）：schema / 能力 / 禁止内容 / PII / 规格。

纯代码规则，命中写 guard_hit，误拦截率可测。
"""
from __future__ import annotations

import re

from packages.agent_core.guardrails.input_guard import RuleSet
from packages.agent_core.types import GuardVerdict, ModelOutput, _GuardName, _HitType
from packages.contracts.enums import IMPORT_ALLOWLIST, ReasonCode


def output_guard(output: ModelOutput, rules: RuleSet) -> GuardVerdict:
    """逐类检查模型输出（禁用的 eval/网络调用、未知 import、PII），命中即返回判据与原文引用。"""
    text = output.content or ""
    # 禁止内容：eval / 未知 import（白名单由契约 IMPORT_ALLOWLIST 单一事实来源给出）
    if re.search(r"\beval\s*\(|new\s+Function\s*\(", text, re.IGNORECASE):
        return GuardVerdict(hit=True, guard=_GuardName.OUTPUT, hit_type=_HitType.FORBIDDEN_EVAL,
                            criterion="OUT-001", source_ref="eval(", reason_code=ReasonCode.GUARD_OUTPUT_HIT)
    if re.search(r"fetch\s*\(|XMLHttpRequest|WebSocket\s*\(", text, re.IGNORECASE):
        return GuardVerdict(hit=True, guard=_GuardName.OUTPUT, hit_type=_HitType.FORBIDDEN_NETWORK,
                            criterion="OUT-002", source_ref="fetch(", reason_code=ReasonCode.GUARD_OUTPUT_HIT)
    # 扫描全部 import：仅放行契约白名单，未知模块视为越权依赖
    for m in re.finditer(r"(?:from|require\s*\()\s*['\"]([^'\"]+)['\"]", text):
        mod = m.group(1)
        # 命中非白名单模块即拦截，避免模型输出引入未授权依赖
        if mod not in IMPORT_ALLOWLIST:
            return GuardVerdict(hit=True, guard=_GuardName.OUTPUT, hit_type=_HitType.UNKNOWN_IMPORT,
                                criterion="OUT-003", source_ref=mod, reason_code=ReasonCode.GUARD_OUTPUT_HIT)
    # PII
    for rule in rules.rules:
        # 复用进向规则集中的 PII 规则，避免正则在两处重复维护
        if rule.hit_type == _HitType.PII:
            m = re.search(rule.pattern, text, re.IGNORECASE)
            # 命中即留证原文片段，便于审计追溯具体泄漏内容
            if m:
                return GuardVerdict(hit=True, guard=_GuardName.OUTPUT, hit_type=_HitType.PII,
                                    criterion=rule.rule_id, source_ref=m.group(0),
                                    reason_code=ReasonCode.GUARD_OUTPUT_HIT)
    return GuardVerdict(hit=False, guard=_GuardName.OUTPUT)
