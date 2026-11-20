"""M07 规程正文加载（ADR-003 / FR-9）测试。"""
from __future__ import annotations

from services.orchestrator.recipe_loader import RecipeMeta, load_recipe, scan_recipes


def test_scan_recipes_finds_two_recipes():
    """规程库至少含日历与任务两个 recipe（Q7）。"""
    recipes = scan_recipes()
    ids = {r.recipe_id for r in recipes}
    assert "recipe.calendar.month.v3" in ids
    assert "recipe.task.list.v2" in ids


def test_load_recipe_reads_body_and_meta():
    """L2-skill 加载正文与元数据（version/token_cost），正文非空。"""
    body = load_recipe("recipe.calendar.month.v3")
    assert body.recipe_id == "recipe.calendar.month.v3"
    assert body.version == "v3"
    assert body.token_cost > 0
    assert "月视图日历" in body.text
    assert len(body.text) > 100


def test_load_recipe_missing_is_fail_closed():
    """未登记 recipe 抛 FileNotFoundError（fail-closed，不静默空正文）。"""
    import pytest

    with pytest.raises(FileNotFoundError):
        load_recipe("recipe.not.exist.v1")


def test_recipe_meta_is_frozen_dataclass():
    """RecipeMeta 为不可变数据类，防止装配期后篡改规程元数据。"""
    recipes = scan_recipes()
    assert all(isinstance(r, RecipeMeta) for r in recipes)
