"""规程变更配置指纹与差异（A16 / §5 D3 验收 / §9.3）。

规则 / 阈值 / 冲突矩阵 / 能力加载上限任一变更 → 触发 M10 回归门禁。
测试用记录型门禁位于 tests/support/guardrails.py；生产触发回调由部署装配注入。
"""

from __future__ import annotations

from dataclasses import dataclass

from services.guardrails import constants
from services.guardrails.patterns import injection, intent, pii


@dataclass(slots=True, frozen=True)
class ConfigSnapshot:
    """可触发回归的配置指纹（§9.3 变更类型集合）。"""

    rule_count: int
    threshold: float
    conflict_count: int
    capability_max: int
    intent_vocab_version: str = ""
    # 规格规则的两类扩展：密度软提示条数 + 特征别名表规模（任一词表变更须触发回归）
    density_caution_count: int = 0
    grid_synonym_count: int = 0


def capture_snapshot() -> ConfigSnapshot:
    """采集当前配置指纹（部署时与基线对比）。"""
    rule_count = len(injection.RULES) + len(pii.RULES) + len(intent.RULES)
    return ConfigSnapshot(
        rule_count=rule_count,
        threshold=constants.SEMANTIC_ROUTE_THRESHOLD,
        conflict_count=len(constants.CONFLICT_RULES),
        capability_max=constants.MAX_CAPABILITY_PER_TURN,
        intent_vocab_version=f"{constants.INTENT_VOCAB_VERSION}:{constants.INTENT_SAMPLES_VERSION}",
        density_caution_count=len(constants.DENSITY_CAUTION_RULES),
        grid_synonym_count=len(constants.GRID_SYNONYMS),
    )


def diff_config(baseline: ConfigSnapshot, current: ConfigSnapshot) -> list[str]:
    """列出发生变更的配置项名；空列表表示无变更。"""
    changed: list[str] = []
    for name in ConfigSnapshot.__dataclass_fields__:
        if getattr(baseline, name) != getattr(current, name):
            changed.append(name)
    return changed
