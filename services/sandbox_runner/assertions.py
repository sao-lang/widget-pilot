"""M08 沙箱四类断言（R9-R12）：尺寸 / 无运行时错误 / 能力走 bridge / 交互行为。

顺序固定由 entry.py 保证；本模块只提供纯函数，零副作用、零模型调用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from packages.agent_core.types import JsonValue
from packages.contracts.enums import GridSpec, WidgetType
from services.sandbox_runner.bridge_stub import evaluate_bridge_events
from services.sandbox_runner.render import RenderMetrics, grid_px

__all__ = [
    "ASSERTION_CASES",
    "assert_capabilities_via_bridge",
    "assert_interactions",
    "assert_no_runtime_error",
    "assert_size",
]


ASSERTION_CASES: tuple[str, ...] = (
    "size_assert",
    "runtime_no_error",
    "capability_via_bridge",
    "calendar_next_month",
    "clock_tick",
    "alarm_toggle",
    "task_check",
)


def assert_size(grid: GridSpec | str, render_metrics: RenderMetrics | Mapping[str, JsonValue]) -> bool:
    """根尺寸必须等于 grid 真实像素，子元素不得越界（2x1 塞月视图拦下）。"""
    expected_w, expected_h = grid_px(grid)
    if isinstance(render_metrics, RenderMetrics):
        width = render_metrics.width_px
        height = render_metrics.height_px
        child_w = render_metrics.max_child_width_px
        child_h = render_metrics.max_child_height_px
    else:
        width = _as_int(render_metrics.get("width_px"))
        height = _as_int(render_metrics.get("height_px"))
        child_w = _as_int(render_metrics.get("max_child_width_px"))
        child_h = _as_int(render_metrics.get("max_child_height_px"))
    return width == expected_w and height == expected_h and child_w <= expected_w and child_h <= expected_h


def assert_no_runtime_error(errors: Sequence[str]) -> bool:
    """挂载/渲染/交互期间不得有未捕获错误。"""
    return len(errors) == 0


def assert_capabilities_via_bridge(
    events: Sequence[Mapping[str, JsonValue]], declared: set[str]
) -> bool:
    """能力调用全走 bridge 且不得越权声明。"""
    return evaluate_bridge_events(events, frozenset(declared)).passed


def assert_interactions(widget_type: WidgetType | str, cases: Sequence[Mapping[str, JsonValue]]) -> bool:
    """四类交互逐条留证；不适用的交互不算失败，适用的必须 passed=True。"""
    del widget_type  # ADR-004 去类型化：交互适用性由容器按 DOM 判定，不按 widget_type 枚举
    return all(
        case.get("passed") is True
        for case in cases
        if case.get("applicable") is True
    )


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1
