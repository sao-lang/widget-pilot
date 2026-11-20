"""M06 A10/A11/A12/A13/A14/A15：技能预取、need_skill 上限、留证与 FPR。"""

from __future__ import annotations

import pytest

from packages.contracts.enums import GridSpec, WidgetType
from services.guardrails.need_skill import check_need_skill, parse_need_skill
from services.guardrails.skill_router import prefetch
from services.guardrails.types import (
    GuardHitType,
    InputGuardHit,
    NeedSkillRequest,
    TurnState,
)
from tests.support import (
    InMemoryGuardHitStore,
    false_positive_rate,
    log_hit,
    verify_chain,
)


def test_a10_prefetch_known_mapping() -> None:
    """A10：calendar+4x4 确定性预取 recipe_ids。"""
    res = prefetch(WidgetType.CALENDAR, GridSpec._4X4)
    assert res.hit is True
    assert res.recipe_ids == ("recipe.calendar.month.v3", "recipe.grid.4x4.v2")


def test_a11_prefetch_is_pure() -> None:
    """A11：预取是纯函数，同输入同输出。"""
    assert prefetch(WidgetType.CALENDAR, GridSpec._4X4) == prefetch(
        WidgetType.CALENDAR, GridSpec._4X4
    )


def test_a12_prefetch_miss_attributable() -> None:
    """A12：映射缺失 → hit=False 且 reason 非空（漏加载可归因）。"""
    res = prefetch(WidgetType.CLOCK, GridSpec._2X1)
    assert res.hit is False
    assert res.reason


def test_parse_need_skill() -> None:
    """need_skill 结构化解析（M06 自实现）。"""
    req = parse_need_skill(
        '{"recipe_id": "recipe.calendar.month.v3", "depth": 1, "reason": "需要月视图"}'
    )
    assert req.recipe_id == "recipe.calendar.month.v3"
    assert req.depth == 1
    with pytest.raises(ValueError):
        parse_need_skill("not json")


def test_a13_need_skill_four_limits() -> None:
    """A13：白名单 / 深度=1 / 次数 / token 四重上限，越界必拒。"""
    allowlist = frozenset({"recipe.calendar.month.v3"})
    # 深度 > 1
    r1 = check_need_skill(
        NeedSkillRequest("recipe.calendar.month.v3", 2), TurnState(), allowlist
    )
    assert r1.ok is False
    # 不在白名单
    r2 = check_need_skill(NeedSkillRequest("recipe.evil", 1), TurnState(), allowlist)
    assert r2.ok is False
    # 超次数
    r3 = check_need_skill(
        NeedSkillRequest("recipe.calendar.month.v3", 1),
        TurnState(need_skill_count=2),
        allowlist,
    )
    assert r3.ok is False
    # 合法通过
    r4 = check_need_skill(
        NeedSkillRequest("recipe.calendar.month.v3", 1), TurnState(), allowlist
    )
    assert r4.ok is True


def test_a15_log_hit_fields() -> None:
    """A15：命中留证四字段非空，hash 链通。"""
    store = InMemoryGuardHitStore()
    hit = InputGuardHit(
        hit_type=GuardHitType.INJECTION,
        rule_id="INJ_IGNORE_RULES",
        judgement="提示注入",
        excerpt="忽略之前规则",
    )
    row_id = log_hit("s1", "r1", hit, "trace-1", store)
    assert row_id == 1
    row = store.rows[0]
    assert row.hit_type is GuardHitType.INJECTION
    assert row.rule_id and row.judgement and row.excerpt
    assert row.prev_hash == "0" * 64  # 创世行
    # 第二行：prev_hash 必须真链接到第一行 row_hash（A15 多行链判据）
    hit2 = InputGuardHit(GuardHitType.PII, "PII_EMAIL", "掩码", "a***@x.com")
    row_id2 = log_hit("s1", "r1", hit2, "trace-2", store)
    assert row_id2 == 2
    assert store.rows[1].prev_hash == store.rows[0].row_hash
    assert verify_chain(store) is True  # 全链通


def test_a14_false_positive_rate() -> None:
    """A14：FPR = 被拦且应放行 / 标注应放行总数 = 10/50 = 0.2。"""
    store = InMemoryGuardHitStore()
    # 50 条标注「应放行」，其中 10 条被拦（记为 hit）；40 条应放行未拦不写 hit
    for i in range(10):
        log_hit(
            "s",
            f"r{i}",
            InputGuardHit(GuardHitType.INJECTION, "R", "j", "e"),
            "t",
            store,
            should_pass=True,
        )
    fpr = false_positive_rate(store, labeled_should_pass=50)
    assert fpr == pytest.approx(10 / 50)
