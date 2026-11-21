"""M06 能力请求校验泛化（ADR-003 / FR-6）红灯测试。"""
from __future__ import annotations

import pytest

from services.guardrails.capability_guard import (
    check_capability_request,
    parse_capability_request,
)
from services.guardrails.types import CapabilityRequest, TurnState


def test_parse_capability_request_level_and_surface():
    """结构化解析 tool/skill 通用的能力请求（capability_id/level/surface）。"""
    req = parse_capability_request(
        '{"capability_id": "tool.attachment.process", "level": 2, "surface": "generation"}'
    )
    assert req.capability_id == "tool.attachment.process"
    assert req.level == 2
    assert req.surface == "generation"
    with pytest.raises(ValueError):
        parse_capability_request("not json")


def test_capability_guard_allows_level1_and_level2():
    """合法 level=1/2 都放行（能力分级披露的核心）。"""
    allowlist = frozenset({"tool.weather", "recipe.calendar.month.v3"})
    r1 = check_capability_request(
        CapabilityRequest("tool.weather", 1, "generation"), TurnState(), allowlist
    )
    r2 = check_capability_request(
        CapabilityRequest("recipe.calendar.month.v3", 2, "generation"), TurnState(), allowlist
    )
    assert r1.ok is True and r2.ok is True


def test_capability_guard_rejects_bad_level_surface_and_not_registered():
    """非法 level / 越权 surface / 未注册 一律拒绝（fail-closed）。"""
    allowlist = frozenset({"tool.weather"})
    assert check_capability_request(
        CapabilityRequest("tool.weather", 3, "generation"), TurnState(), allowlist
    ).ok is False
    assert check_capability_request(
        CapabilityRequest("tool.weather", 2, "widget-runtime"), TurnState(), allowlist
    ).ok is False
    assert check_capability_request(
        CapabilityRequest("eval", 2, "generation"), TurnState(), allowlist
    ).ok is False


def test_capability_guard_counts_turn_limits():
    """次数与 token 超限拒绝；通过则计数累加（预算不可绕过）。"""
    allowlist = frozenset({"tool.weather"})
    turn = TurnState(capability_count=2, capability_tokens=0)
    r = check_capability_request(
        CapabilityRequest("tool.weather", 2, "generation"), turn, allowlist, token_cost=10
    )
    assert r.ok is False
    fresh = TurnState()
    r2 = check_capability_request(
        CapabilityRequest("tool.weather", 2, "generation"), fresh, allowlist, token_cost=10
    )
    assert r2.ok is True
