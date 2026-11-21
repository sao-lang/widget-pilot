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


def capture_snapshot() -> ConfigSnapshot:
    """采集当前配置指纹（部署时与基线对比）。"""
    rule_count = len(injection.RULES) + len(pii.RULES) + len(intent.RULES)
    return ConfigSnapshot(
        rule_count=rule_count,
        threshold=constants.SEMANTIC_ROUTE_THRESHOLD,
        conflict_count=len(constants.CONFLICT_RULES),
        capability_max=constants.MAX_CAPABILITY_PER_TURN,
    )


def diff_config(baseline: ConfigSnapshot, current: ConfigSnapshot) -> list[str]:
    """列出发生变更的配置项名；空列表表示无变更。"""
    changed: list[str] = []
    for name in ConfigSnapshot.__dataclass_fields__:
        if getattr(baseline, name) != getattr(current, name):
            changed.append(name)
    return changed
