"""ADR-003 / B4+B5：能力结果落 store + token 累加（复审 Should 2 证据）。"""
from __future__ import annotations

from pydantic import BaseModel

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.tools.registry import (
    REGISTRY,
    Capability,
    register,
    unfreeze_registry,
)
from packages.agent_core.types import Input, SpecState, State


class Args(BaseModel):
    x: str = "ok"


def _handler(args: Args) -> str:
    return "CAPABILITY-OUTPUT"


def test_capability_result_stored_and_tokens_accumulated(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider, object_store
):
    """L2 结果落 ObjectStore、ref 纯 key 不内联、capability_tokens 累加。"""
    unfreeze_registry()
    REGISTRY.clear()
    register(
        "tool.probe",
        Capability(
            id="tool.probe", kind="tool", brief="探针", detail_ref="args", payload_ref="handler.probe",
            surfaces=frozenset({"generation"}), cost_hint=1, args_model=Args, handler=_handler,
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
    s = State.initial("s-res", "run-res", "x")
    s = s.bump(spec=SpecState(intent="x", signal="LOAD_CAPABILITY", capability_id="tool.probe", level=2))
    checkpoint_store.states["run-res"] = s
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-1"}'])
    out = run("s-res", "run-res", Input(text="x"), deps)
    assert out.spec.capability_tokens > 0  # B4：token 累加
    assert out.spec.capability_count == 1
    assert out.spec.tool_result_ref and "CAPABILITY-OUTPUT" not in out.spec.tool_result_ref  # B5：纯 key
    assert object_store.data, "结果应落对象存储"
    unfreeze_registry()
    REGISTRY.clear()
