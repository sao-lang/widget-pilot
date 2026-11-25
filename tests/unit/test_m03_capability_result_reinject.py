"""ADR-014 / §8.2：L2 结果回灌进 prompt，且前缀槽字节不变。"""
from __future__ import annotations

import json

from pydantic import BaseModel

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.tools.registry import (
    REGISTRY,
    Capability,
    register,
    unfreeze_registry,
)
from packages.agent_core.types import Input, Message, SpecState, State


class Args(BaseModel):
    x: str = "ok"


def _handler(args: Args) -> str:
    return '{"answer":"TOOL-RESULT-VISIBLE"}'


def test_capability_result_reinjected_into_messages(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider, object_store
):
    """L2 输出作为不可信数据消息进 messages，下一轮模型调用看得到。"""
    unfreeze_registry()
    REGISTRY.clear()
    register(
        "tool.reinject",
        Capability(
            id="tool.reinject", kind="tool", brief="回灌探针",
            detail_ref="args", payload_ref="handler",
            surfaces=frozenset({"generation"}), cost_hint=1,
            args_model=Args, handler=_handler,
        ),
    )
    deps = Deps(
        model=fake_model,
        checkpoint_store=checkpoint_store,
        idempotency_store=idem_store,
        event_bus=event_bus,
        clock=clock,
        tracer=tracer,
        lease_provider=lease_provider,
        object_store=object_store,
    )
    # 首轮装配固定槽位（模拟生产 catalog），记录前缀字节
    s = State.initial("s-rein", "run-rein", "x")
    s = s.bump(
        spec=SpecState(
            intent="x",
            signal="LOAD_CAPABILITY",
            capability_id="tool.reinject",
            level=2,
            messages=[
                Message("system", "系统契约：仅生成符合 WidgetSpec 的 TSX/React 控件；import 限白名单。")
            ],
        )
    )
    checkpoint_store.states["run-rein"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-1"}'])
    out = run("s-rein", "run-rein", Input(text="x"), deps)

    msgs = out.spec.messages
    assert len(msgs) == 2
    assert msgs[0].content.startswith("系统契约")  # 前缀槽不变
    assert msgs[-1].content.startswith("[工具结果·不可信数据]")
    assert "TOOL-RESULT-VISIBLE" in msgs[-1].content
    # 模型下一轮收到的最后一条正是回灌结果
    assert fake_model.calls
    assert any("TOOL-RESULT-VISIBLE" in json.dumps(call["messages"], default=str) for call in fake_model.calls)
    unfreeze_registry()
    REGISTRY.clear()


def test_denial_feedback_uses_same_prefix(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider, object_store
):
    """拒绝路径也走统一回灌入口，模型可自纠正。"""
    from packages.agent_core.tools.registry import register as reg

    unfreeze_registry()
    REGISTRY.clear()
    reg(
        "tool.denied",
        Capability(
            id="tool.denied", kind="tool", brief="拒绝探针",
            detail_ref="args", payload_ref="handler",
            surfaces=frozenset({"other"}), cost_hint=1,
            args_model=Args, handler=_handler,
        ),
    )
    deps = Deps(
        model=fake_model,
        checkpoint_store=checkpoint_store,
        idempotency_store=idem_store,
        event_bus=event_bus,
        clock=clock,
        tracer=tracer,
        lease_provider=lease_provider,
        object_store=object_store,
    )
    s = State.initial("s-den", "run-den", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.denied", level=2))
    checkpoint_store.states["run-den"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-den"}'])
    out = run("s-den", "run-den", Input(text="x"), deps)
    assert out.spec.messages[-1].content.startswith("[工具结果·不可信数据]")
    assert "能力加载被拒" in out.spec.messages[-1].content
    unfreeze_registry()
    REGISTRY.clear()
