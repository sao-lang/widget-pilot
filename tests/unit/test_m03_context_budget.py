"""M03 context + budget：T10 RRF 融合确定性 / T23 四维预算触顶 / 槽位装配。"""
from __future__ import annotations

from typing import cast

import pytest

from packages.agent_core.budget.meter import add, check
from packages.agent_core.constants import DEFAULT_BUDGET_LIMITS
from packages.agent_core.context.budget import assemble_messages, compress_tool_messages
from packages.agent_core.context.slots import assemble, rrf_fuse
from packages.agent_core.errors import CoreError
from packages.agent_core.types import BudgetUsage, Candidate, Message, SlotConfig, State


# ---- T10：RRF 融合确定性 ----
def test_T10_rrf_deterministic():
    vector = [Candidate("d1", "a", 0.9), Candidate("d3", "c", 0.7), Candidate("d2", "b", 0.5)]
    bm25 = [Candidate("d2", "b", 12.0), Candidate("d1", "a", 10.0)]
    r1 = rrf_fuse(vector, bm25)
    r2 = rrf_fuse(vector, bm25)
    assert [c.doc_id for c in r1] == [c.doc_id for c in r2]  # 两次完全一致
    # d1 在两榜都靠前 → 排第一；d2 在 bm25 第一 → 靠前
    assert r1[0].doc_id == "d1"


def test_T10_rrf_k_fixed():
    from packages.agent_core.constants import RRF_K
    assert RRF_K == 60
    # 单榜单元素得分 = 1/(k+1)
    r = rrf_fuse([Candidate("only", "x", 1.0)], [])
    assert abs(r[0].score - 1.0 / (RRF_K + 1)) < 1e-9


# ---- T23：四维预算任一触顶确定性中断 ----
@pytest.mark.parametrize("dim", ["tokens", "iterations", "tool_calls", "sandbox_minutes"])
def test_T23_budget_exhaustion(dim):
    s = State.initial("s", "run", "x")
    # 四维用量经可变数值字典构造，再 cast 回确定键 BudgetUsage
    used = cast(dict[str, float], {"tokens": 0, "iterations": 0, "tool_calls": 0, "sandbox_minutes": 0.0})
    used[dim] = DEFAULT_BUDGET_LIMITS[dim]  # 恰好触顶
    s = s.bump(budget_used=cast(BudgetUsage, used))
    with pytest.raises(CoreError) as ei:
        check(s)
    assert ei.value.code == "BUDGET_EXHAUSTED"
    assert ei.value.retryable is False


def test_budget_under_limit_ok():
    s = State.initial("s", "run", "x")
    check(s)  # 全 0 用量不应抛异常


def test_budget_add_accumulates_four_dims():
    from packages.agent_core.types import Usage
    s = State.initial("s", "run", "x")
    s = add(s, usage=Usage(100, 50), tier="standard", tool_calls=2, sandbox_minutes=1.5)
    assert s.budget_used["tokens"] == 150
    assert s.budget_used["tool_calls"] == 2
    assert s.budget_used["sandbox_minutes"] == 1.5
    assert s.budget_used["iterations"] == 1
    assert s.tier_history == ["standard"]
    check(s)  # 未触顶不抛


# ---- 槽位装配：固定顺序保住 prompt caching ----
def test_slot_order_fixed():
    s = State.initial("s", "run", "做一个日历", "4x4")
    s = s.bump(excluded_approaches=["方案A"])
    msgs = assemble(s, SlotConfig(include_memory=False, include_retrieval=False))
    assert msgs[0].role == "system"       # 系统契约在前
    assert any("方案A" in m.content for m in msgs)  # excluded_approaches 保留
    roles = [m.role for m in msgs]
    assert roles.index("system") < roles.index("user")


def test_l0_catalog_slot_after_system_contract():
    """ADR-003 / FR-8：L0 目录槽位固定在系统契约之后、规程之前。"""
    s = State.initial("s", "run", "做一个日历", "4x4")
    catalog = ({"id": "tool.weather", "kind": "tool", "brief": "查天气", "cost_hint": 10},)
    msgs = assemble(
        s,
        SlotConfig(include_memory=False, include_retrieval=False, capability_catalog=catalog),
    )
    contents = [m.content for m in msgs]
    system_idx = next(i for i, c in enumerate(contents) if c.startswith("系统契约"))
    catalog_idx = next(i for i, c in enumerate(contents) if c.startswith("可用能力"))
    recipe_idx = next(i for i, c in enumerate(contents) if c.startswith("规程"))
    assert system_idx < catalog_idx < recipe_idx
    assert "tool.weather" in contents[catalog_idx]
    assert "args" not in contents[catalog_idx]  # L0 不泄漏 schema


def test_assemble_messages_uses_slots():
    s = State.initial("s", "run", "x")
    msgs = assemble_messages(s)
    assert all(isinstance(m, Message) for m in msgs)
    assert len(msgs) >= 2


def test_compress_preserves_excluded_approaches():
    s = State.initial("s", "run", "x")
    s = s.bump(excluded_approaches=["必须保留的方案"])
    out = compress_tool_messages(s, max_chars=10)
    assert "必须保留的方案" in out
