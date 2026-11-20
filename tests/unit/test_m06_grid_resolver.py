"""M06 A6/A7/A19/A20/A21/A26：规格获取三级来源与冲突矩阵。"""

from __future__ import annotations

from packages.contracts.enums import GridSpec
from services.guardrails.grid_resolver import resolve_grid
from services.guardrails.spec_conflicts import check_conflict
from services.guardrails.types import GridSource, GridStatus


def test_a20_explicit_text_grid() -> None:
    """A20：描述线索解析 grid，source=explicit_text。"""
    res = resolve_grid(None, "做一个 4x4 的月视图日历")
    assert res.grid is GridSpec._4X4
    assert res.status is GridStatus.EXPLICIT_TEXT
    assert res.grid_source is GridSource.EXPLICIT_TEXT


def test_selector_grid() -> None:
    """选择器优先：带 grid 直接用。"""
    res = resolve_grid(GridSpec._2X1, "做一个日历")
    assert res.grid is GridSpec._2X1
    assert res.status is GridStatus.SELECTOR
    assert res.grid_source is GridSource.SELECTOR


def test_a19_missing_asks() -> None:
    """A19：无 grid 无线索 → missing，suggest_grid 非空，不推断。"""
    res = resolve_grid(None, "做一个能翻月的日历")
    assert res.status is GridStatus.MISSING
    assert res.grid is None
    assert res.grid_source is None
    assert len(res.suggest_grid) > 0


def test_a26_preferred_grids_first() -> None:
    """A26：preferred_grids 合法项置顶 suggest_grid。"""
    res = resolve_grid(None, "做一个能翻月的日历", preferred_grids=["2x2", "4x4"])
    assert res.suggest_grid[0] is GridSpec._2X2


def test_a7_conflict_2x1_month() -> None:
    """A7：2x1 + 月视图冲突，建议 4x4。"""
    assert check_conflict(GridSpec._2X1, "月视图") is True
    assert check_conflict(GridSpec._4X4, "月视图") is False


def test_invalid_grid_rejected() -> None:
    """A6：非法 grid 枚举被拒（resolve 只接受 GridSpec，字符串非法 → 抛错）。"""
    import pytest

    with pytest.raises(ValueError):
        resolve_grid("9x9", "做一个日历")  # type: ignore[arg-type]
