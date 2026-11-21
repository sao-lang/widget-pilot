"""M03 统一 Capability 抽象 + 渐进式披露（ADR-003 / FR-1~5）红灯测试。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from packages.agent_core.errors import CoreError
from packages.agent_core.tools.invoke import load
from packages.agent_core.tools.registry import (
    REGISTRY,
    Capability,
    capability_detail,
    level0,
    register,
    unfreeze_registry,
)
from packages.agent_core.types import Decision, DecisionKind


class Args(BaseModel):
    city: str


def _handler(args: Args) -> str:
    return f"weather:{args.city}"


@pytest.fixture(autouse=True)
def clean_registry():
    """每个用例隔离 registry：注册两个能力后清理，避免跨测试污染。"""
    unfreeze_registry()
    REGISTRY.clear()
    register(
        "tool.weather",
        Capability(
            id="tool.weather",
            kind="tool",
            brief="查天气",
            detail_ref="args.model",
            payload_ref="handler.weather",
            surfaces=frozenset({"generation"}),
            cost_hint=10,
            args_model=Args,
            handler=_handler,
            max_output_chars=6,
        ),
    )
    register(
        "recipe.calendar.month.v3",
        Capability(
            id="recipe.calendar.month.v3",
            kind="skill",
            brief="月视图日历规程",
            detail_ref="manifest.calendar",
            payload_ref="body.calendar.v3",
            surfaces=frozenset({"generation"}),
            cost_hint=200,
        ),
    )
    yield
    unfreeze_registry()
    REGISTRY.clear()


def test_registry_is_unified_capability():
    """FR-1/2：REGISTRY 值是 Capability，两族 kind 共存于一个注册表。"""
    assert all(isinstance(spec, Capability) for spec in REGISTRY.values())
    assert {spec.kind for spec in REGISTRY.values()} == {"tool", "skill"}


def test_level0_has_only_brief_fields():
    """FR-2/5：L0 目录只含 id/kind/brief/cost_hint，不含 schema/正文/handler。"""
    catalog = level0()
    weather = next(item for item in catalog if item["id"] == "tool.weather")
    assert set(weather) == {"id", "kind", "brief", "cost_hint"}
    assert "detail_ref" not in weather and "payload_ref" not in weather


def test_level1_detail_without_execution():
    """FR-2：L1 返回详情引用，不执行 handler（调用计数为 0）。"""
    calls: list[str] = []

    def counting(args: Args) -> str:
        calls.append(args.city)
        return "x"

    register(
        "tool.counting",
        Capability(
            id="tool.counting",
            kind="tool",
            brief="计数",
            detail_ref="args.model",
            payload_ref="handler.counting",
            surfaces=frozenset({"generation"}),
            cost_hint=1,
            args_model=Args,
            handler=counting,
        ),
    )
    detail = capability_detail("tool.weather")
    assert detail["detail_ref"] == "args.model"
    assert calls == []


def test_load_level2_tool_executes_real_handler():
    """FR-4：L2-tool 真实调用 handler 并截断（替换旧假 ref 回灌）。"""
    result = load(
        Decision(
            DecisionKind.LOAD_CAPABILITY,
            capability_id="tool.weather",
            level=2,
            args={"city": "Shenzhen"},
            surface="generation",
        )
    )
    assert result.ok is True
    assert result.output == "weathe"  # max_output_chars=6
    assert result.truncated is True


def test_load_level2_skill_returns_body_not_executed():
    """FR-2：L2-skill 返回正文引用，当作数据，不执行。"""
    result = load(
        Decision(
            DecisionKind.LOAD_CAPABILITY,
            capability_id="recipe.calendar.month.v3",
            level=2,
            surface="generation",
        )
    )
    assert result.ok is True
    assert "body.calendar.v3" in result.output


def test_load_handler_valueerror_becomes_coreerror():
    """handler 抛 ValueError（如 S3 无 hash/篡改）→ CoreError 业务失败，不逃逸。"""
    def bad_ref(args: Args) -> str:
        raise ValueError("attachment ref missing sha256 hash")

    register(
        "tool.bad_ref",
        Capability(
            id="tool.bad_ref", kind="tool", brief="坏引用", detail_ref="args",
            payload_ref="handler", surfaces=frozenset({"generation"}), cost_hint=1,
            args_model=Args, handler=bad_ref,
        ),
    )
    with pytest.raises(CoreError) as ei:
        load(Decision(
            DecisionKind.LOAD_CAPABILITY, capability_id="tool.bad_ref", level=2,
            args={"city": "SZ"}, surface="generation",
        ))
    assert ei.value.code == "CONTRACT_MISMATCH"
    assert ei.value.retryable is False


def test_load_handler_oserror_becomes_infra():
    """handler 抛 OSError（如 S3 网络故障）→ CoreError infra，走重试。"""
    def down(args: Args) -> str:
        raise OSError("S3 unavailable")

    register(
        "tool.down",
        Capability(
            id="tool.down", kind="tool", brief="故障", detail_ref="args",
            payload_ref="handler", surfaces=frozenset({"generation"}), cost_hint=1,
            args_model=Args, handler=down,
        ),
    )
    with pytest.raises(CoreError) as ei:
        load(Decision(
            DecisionKind.LOAD_CAPABILITY, capability_id="tool.down", level=2,
            args={"city": "SZ"}, surface="generation",
        ))
    assert ei.value.code == "SANDBOX_UNAVAILABLE"
    assert ei.value.retryable is True


def test_load_unregistered_rejected():
    """FR-6：未注册能力被拒。"""
    with pytest.raises(CoreError) as ei:
        load(Decision(DecisionKind.LOAD_CAPABILITY, capability_id="eval", level=2))
    assert ei.value.code == "CAPABILITY_DENIED"
