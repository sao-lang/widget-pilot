"""P1 #42：确定性命中分支终态事件发射器（生产工厂）测试。"""
from __future__ import annotations

from typing import cast

from packages.agent_core.injection import Deps
from packages.agent_core.types import Decision, DecisionKind, JsonObject, State
from packages.contracts.enums import AgentEventKind
from services.orchestrator.dispatcher_events import make_terminal_event_emitter
from tests.conftest import (
    FakeClock,
    FakeModel,
    InMemoryEventBus,
    InMemoryIdempotencyStore,
)


def _payload(event) -> JsonObject:
    """AgentEvent.payload 是 JsonValue 联合；断言前收窄为 dict。"""
    p = event.payload
    assert isinstance(p, dict)
    return cast(JsonObject, p)


def _deps() -> Deps:
    return Deps(
        model=FakeModel(),
        checkpoint_store=None,  # type: ignore[arg-type]
        idempotency_store=InMemoryIdempotencyStore(),
        event_bus=InMemoryEventBus(),
        clock=FakeClock(),
        lease_provider=None,  # type: ignore[arg-type]
        owner="test",
    )


def test_done_terminal_emits_done_event() -> None:
    deps = _deps()
    state = State.initial("s1", "r1", "做一个日历").terminate(
        Decision(DecisionKind.DONE), bundle_ref="bundle-1"
    )
    make_terminal_event_emitter(deps)(state)
    events = deps.event_bus.all()  # type: ignore[attr-defined]
    assert len(events) == 1
    assert events[0].kind == AgentEventKind.DONE.value
    term = _payload(events[0])["terminal"]
    assert isinstance(term, dict) and term["bundle_ref"] == "bundle-1"
    assert events[0].seq == 0


def test_failed_terminal_emits_error_event() -> None:
    deps = _deps()
    state = State.initial("s1", "r1", "越界").terminate(
        Decision(DecisionKind.FAILED, reason_code="INTENT_OUT_OF_SCOPE"),
        evidence_ref="evidence/s1/r1",
    )
    make_terminal_event_emitter(deps)(state)
    events = deps.event_bus.all()  # type: ignore[attr-defined]
    assert len(events) == 1
    assert events[0].kind == AgentEventKind.ERROR.value


def test_clarification_emits_ask_user_event() -> None:
    deps = _deps()
    state = State.initial("s1", "r1", "桌面上加个卡片").pause("SPEC_MISSING:grid")
    make_terminal_event_emitter(deps)(state)
    events = deps.event_bus.all()  # type: ignore[attr-defined]
    assert len(events) == 1
    assert events[0].kind == AgentEventKind.ASK_USER.value
    assert "question" in _payload(events[0])


def test_seq_increments_with_existing_events() -> None:
    deps = _deps()
    state = State.initial("s1", "r1", "做一个日历").terminate(
        Decision(DecisionKind.DONE), bundle_ref="bundle-1"
    )
    emitter = make_terminal_event_emitter(deps)
    emitter(state)
    emitter(state)
    events = deps.event_bus.all()  # type: ignore[attr-defined]
    assert [e.seq for e in events] == [0, 1]


def test_clarification_event_carries_precise_choices() -> None:
    """NB2（review Round2）：SSE ASK_USER payload 带精准候选（前端可渲染）。"""
    import json

    from services.orchestrator.request_context import clarification_payload_var

    deps = _deps()
    state = State.initial("s1", "r1", "把第二个日历改成4x4").pause("TARGET_AMBIGUOUS")
    payload = {
        "reason": "TARGET_AMBIGUOUS", "question_kind": "target",
        "choices": [{"bundle_id": "b1", "grid": "4x2"}], "missing_slots": ["target_bundle"],
    }
    token = clarification_payload_var.set(json.dumps(payload))
    try:
        make_terminal_event_emitter(deps)(state)
    finally:
        clarification_payload_var.reset(token)
    events = deps.event_bus.all()  # type: ignore[attr-defined]
    assert events[0].kind == AgentEventKind.ASK_USER.value
    body = _payload(events[0])
    assert body["question"] == "TARGET_AMBIGUOUS"
    assert body["question_kind"] == "target"
    choices = body["choices"]
    assert isinstance(choices, list) and isinstance(choices[0], dict)
    assert choices[0]["bundle_id"] == "b1"
