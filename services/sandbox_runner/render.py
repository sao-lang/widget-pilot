"""M08 渲染结果结构（R8）：把沙箱内 jsdom 挂载结果收窄为可断言字段。

不模拟真实设备、不接系统 API；尺寸来自 WidgetRuntime card.px（cell=80px）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from packages.agent_core.types import JsonValue
from packages.contracts.enums import CELL_PX, GridSpec

__all__ = ["RenderMetrics", "grid_px", "parse_render_stage"]


@dataclass(slots=True, frozen=True)
class RenderMetrics:
    """沙箱内挂载渲染的可断言指标（像素与错误）。"""

    grid: str
    width_px: int
    height_px: int
    max_child_width_px: int
    max_child_height_px: int
    errors: tuple[str, ...]


def grid_px(grid: GridSpec | str) -> tuple[int, int]:
    """按 cell=80px 把六值规格映射为真实卡片像素。"""
    value = grid.value if isinstance(grid, GridSpec) else grid
    cols, _, rows = value.partition("x")
    if not cols.isdigit() or not rows.isdigit():
        raise ValueError(f"invalid grid: {value}")
    return int(cols) * CELL_PX, int(rows) * CELL_PX


def parse_render_stage(stage: Mapping[str, JsonValue], expected_grid: str) -> RenderMetrics:
    """把容器 JSON 的 render 阶段收窄为 RenderMetrics；字段缺失按失败登记。"""
    errors = stage.get("errors", ())
    error_tuple = tuple(str(item) for item in errors) if isinstance(errors, (list, tuple)) else ("render_stage_invalid",)
    return RenderMetrics(
        grid=str(stage.get("grid", expected_grid)),
        width_px=_as_int(stage.get("width_px")),
        height_px=_as_int(stage.get("height_px")),
        max_child_width_px=_as_int(stage.get("max_child_width_px")),
        max_child_height_px=_as_int(stage.get("max_child_height_px")),
        errors=error_tuple,
    )


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return -1
