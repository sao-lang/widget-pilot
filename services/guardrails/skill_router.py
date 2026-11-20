"""技能确定性预取（§3.4 D）：(widget_type, grid) → recipe_id[] 静态映射。

纯函数、同输入同输出；未命中留 reason 支撑「技能漏加载可归因」（A12）。
"""

from __future__ import annotations

from packages.contracts.enums import GridSpec, WidgetType
from services.guardrails.types import PrefetchResult

# (widget_type, grid) → recipe_ids（静态映射，M10 维护规程库，M06 只读映射）
_PREFETCH_MAP: dict[tuple[WidgetType, GridSpec], tuple[str, ...]] = {
    (WidgetType.CALENDAR, GridSpec._4X4): (
        "recipe.calendar.month.v3",
        "recipe.grid.4x4.v2",
    ),
    (WidgetType.CALENDAR, GridSpec._2X4): ("recipe.calendar.month.v3",),
    (WidgetType.CLOCK, GridSpec._2X2): ("recipe.clock.dial.v2",),
    (WidgetType.CLOCK, GridSpec._4X4): ("recipe.clock.dial.v2", "recipe.grid.4x4.v2"),
    (WidgetType.ALARM, GridSpec._2X1): ("recipe.alarm.basic.v1",),
    (WidgetType.TASK, GridSpec._4X2): ("recipe.task.list.v2",),
}


def prefetch(widget_type: WidgetType, grid: GridSpec) -> PrefetchResult:
    """按 (widget_type, grid) 查静态映射，返回 recipe_ids。"""
    recipes = _PREFETCH_MAP.get((widget_type, grid))
    if recipes is None:
        return PrefetchResult(
            recipe_ids=(),
            hit=False,
            reason=f"预取映射缺 {widget_type.value}+{grid.value} 条目",
        )
    return PrefetchResult(recipe_ids=recipes, hit=True)
