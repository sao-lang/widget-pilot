"""规格获取三级来源（§3.4 G / ADR-001 D-6）：选择器 → 描述线索 → 缺省询问。

未说明的 grid 绝不推断：前两级皆无则 status=missing、grid_source=None，
suggest_grid 非空供用户点选（v1.4 D-12：preferred_grids 合法项置顶，只影响顺序）。
"""

from __future__ import annotations

import re

from packages.contracts.enums import GridSpec, WidgetType
from services.guardrails.constants import (
    CONFLICT_RULES,
    GRID_HINT_PATTERNS,
    WIDGET_HINTS,
    WIDGET_TYPICAL_GRIDS,
)
from services.guardrails.spec_conflicts import check_conflict
from services.guardrails.types import GridResolution, GridSource, GridStatus

_SUGGEST_LIMIT = 3


def _detect_widget(description: str) -> WidgetType | None:
    """从描述抽取控件类型（首个命中），用于密度建议。"""
    for pattern, widget in WIDGET_HINTS:
        if re.search(pattern, description, flags=re.IGNORECASE):
            return widget
    return None


def _detect_conflict_features(description: str) -> tuple[str, ...]:
    """从描述抽所有冲突矩阵特征词（§3.4 G：全特征，非仅 calendar+月视图）。

    特征词来自 CONFLICT_RULES 的 feature 列；同义词见 semantic._detect_feature。
    """
    features: list[str] = []
    synonyms = {
        "月视图": ("月视图", "翻月", "能翻月"),
        "多行列表": ("多行列表", "列表"),
        "宽表格": ("宽表格", "表格"),
    }
    known = {feature for _grid, feature, _judg, _sug in CONFLICT_RULES}
    for feature in known:
        words = synonyms.get(feature, (feature,))
        if any(word in description for word in words):
            features.append(feature)
    return tuple(features)


def _dedup(items: list[GridSpec]) -> tuple[GridSpec, ...]:
    """按首次出现顺序去重，保留最多 _SUGGEST_LIMIT 项。"""
    seen: set[GridSpec] = set()
    result: list[GridSpec] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result[:_SUGGEST_LIMIT])


def resolve_grid(
    grid: GridSpec | None,
    description: str,
    preferred_grids: list[str] | None = None,
) -> GridResolution:
    """按三级来源确定 grid。

    Args:
        grid: 请求体选择器传入的规格（可选）。
        description: 用户描述。
        preferred_grids: 用户偏好规格字符串列表（v1.4，只影响建议顺序）。

    Returns:
        GridResolution；status 只描述本次解析路径，grid_source 是溯源字段。

    Raises:
        ValueError: 传入的 grid 或 preferred_grids 不是合法 GridSpec 枚举。
    """
    # 一级：选择器（非法枚举直接拒绝，不静默兜底）
    if grid is not None:
        if not isinstance(grid, GridSpec):
            raise ValueError(f"非法 grid: {grid!r}")
        return GridResolution(
            status=GridStatus.SELECTOR,
            grid=grid,
            grid_source=GridSource.SELECTOR,
        )

    # 二级：描述线索词表（规则确定性解析，不调用模型）
    for pattern, hinted in GRID_HINT_PATTERNS:
        if re.search(pattern, description, flags=re.IGNORECASE):
            return GridResolution(
                status=GridStatus.EXPLICIT_TEXT,
                grid=hinted,
                grid_source=GridSource.EXPLICIT_TEXT,
            )

    # 三级：缺省 → 询问用户，构造建议（偏好置顶 → 冲突过滤 → 密度补齐）
    widget = _detect_widget(description)
    candidates: list[GridSpec] = []
    for raw in preferred_grids or []:
        try:
            candidates.append(GridSpec(raw))
        except ValueError as exc:
            raise ValueError(f"非法 preferred grid: {raw!r}") from exc
    # 过滤与控件特征冲突的候选（§3.4 G：全特征矩阵，非仅 calendar+月视图）
    # 从描述抽所有冲突特征词，任一命中即过滤该组合
    features = _detect_conflict_features(description)
    candidates = [
        c for c in candidates if not any(check_conflict(c, f) for f in features)
    ]
    typical = [
        t
        for t in WIDGET_TYPICAL_GRIDS.get(widget or WidgetType.CUSTOM, ())
        if not any(check_conflict(t, f) for f in features)
    ]
    candidates.extend(typical)
    return GridResolution(
        status=GridStatus.MISSING,
        grid=None,
        grid_source=None,
        suggest_grid=_dedup(candidates),
    )
