"""M03 事件 seq 生成与 emit（§4.2 / §4.1 L21/L22）。

seq 每会话单调递增；显式 done/error；由注入的 EventBus 落事件窗口。
"""
from __future__ import annotations

from datetime import UTC, datetime

from packages.agent_core.injection import EventBus
from packages.agent_core.types import (
    AgentEvent,
    Decision,
    DecisionKind,
    JsonValue,
    State,
)
from packages.contracts.enums import AgentEventKind


def next_seq(events: list[AgentEvent], session_id: str) -> int:
    """从现有事件推出下一 seq（每会话单调）。空会话从 0 起。"""
    seqs = [e.seq for e in events if e.session_id == session_id]
    return max(seqs) + 1 if seqs else 0


def decision_to_kind(decision: Decision, state: State | None = None) -> AgentEventKind:
    """Decision → AgentEventKind（kind 属于契约枚举，18 种之一；规则 5 用枚举成员）。"""
    k = decision.kind
    if k == DecisionKind.DONE:
        return AgentEventKind.DONE
    if k in (DecisionKind.FAILED, DecisionKind.HANDOFF):
        return AgentEventKind.ERROR
    if k == DecisionKind.ASK_USER:
        return AgentEventKind.ASK_USER
    if k in (DecisionKind.AWAIT_INPUT, DecisionKind.RESUME_FROM_CLARIFICATION):
        return AgentEventKind.INTERRUPTED
    if k == DecisionKind.REPAIR:
        return AgentEventKind.VALIDATION
    if k == DecisionKind.CALL_TOOL:
        return AgentEventKind.NOTICE
    return AgentEventKind.PLAN  # CALL_MODEL


def emit(
    seq: int,
    phase: str,
    decision: Decision,
    *,
    bus: EventBus,
    session_id: str,
    run_id: str,
    trace_id: str,
    payload: JsonValue = None,
    now: datetime | None = None,
    kind: str | None = None,
) -> AgentEvent:
    """§11.3 调用形态：emit(state.seq, state.phase, decision)。"""
    event = AgentEvent(
        seq=seq,
        session_id=session_id,
        run_id=run_id,
        ts=(now or datetime.now(UTC)).isoformat(),
        kind=kind or decision_to_kind(decision),
        phase=str(phase),
        payload=payload if payload is not None else {"decision": decision.kind},
        trace_id=trace_id,
    )
    bus.emit(event)
    return event
