"""规格冲突矩阵（§3.4 C）：(grid, 意图特征) → conflict，spec 前置提示。"""

from __future__ import annotations

from packages.contracts.enums import GridSpec
from services.guardrails.constants import CONFLICT_RULES


def check_conflict(grid: GridSpec, intent_feature: str) -> bool:
    """判断给定规格与意图特征是否冲突。

    Args:
        grid: 已确定的规格枚举。
        intent_feature: 从描述抽取的意图特征（如「月视图」「宽表格」）。

    Returns:
        True 表示冲突，需在 spec 生成前提示改选。
    """
    return any(
        conflict_grid is grid and feature in intent_feature
        for conflict_grid, feature, _judgement, _suggest in CONFLICT_RULES
    )


def suggest_for(grid: GridSpec, intent_feature: str) -> GridSpec | None:
    """返回冲突时的建议规格；无冲突或未覆盖返回 None。"""
    for conflict_grid, feature, _judgement, suggest in CONFLICT_RULES:
        if conflict_grid is grid and feature in intent_feature:
            return suggest
    return None
