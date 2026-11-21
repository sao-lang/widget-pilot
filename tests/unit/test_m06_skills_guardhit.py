"""M06 A14/A15：命中留证与 FPR（A10-A12 预取、A13 need_skill 已删除）。"""

from __future__ import annotations

import pytest

from services.guardrails.types import GuardHitType, InputGuardHit
from tests.support import (
    InMemoryGuardHitStore,
    false_positive_rate,
    log_hit,
    verify_chain,
)


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
