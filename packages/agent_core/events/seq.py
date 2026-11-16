"""M03 事件 seq 生成与 emit（§4.2 / §4.1 L21/L22）。

seq 每会话单调递增；显式 done/error；由注入的 EventBus 落事件窗口。
"""
from __future__ import annotations

from datetime import UTC, datetime

from packages.agent_core.injection import EventBus
from packages.agent_core.types import AgentEvent, Decision, State


def next_seq(events: list[AgentEvent], session_id: str) -> int:
    """从现有事件推出下一 seq（每会话单调）。空会话从 0 起。"""
    seqs = [e.seq for e in events if e.session_id == session_id]
    return max(seqs) + 1 if seqs else 0


def decision_to_kind(decision: Decision, state: State) -> str:
    """Decision → AgentEventKind（kind 属于契约枚举，18 种之一）。"""
    k = decision.kind
    if k == "DONE":
        return "done"
    if k in ("FAILED", "HANDOFF"):
        return "error"
    if k == "ASK_USER":
        return "ask_user"
    if k in ("AWAIT_INPUT", "RESUME_FROM_CLARIFICATION"):
        return "interrupted"
    if k == "REPAIR":
        return "validation"
    if k == "CALL_TOOL":
        return "notice"
    return "plan"  # CALL_MODEL


def emit(
    seq: int,
    phase: str,
    decision: Decision,
    *,
    bus: EventBus,
    session_id: str,
    run_id: str,
    trace_id: str,
    payload: object = None,
    now: datetime | None = None,
    kind: str | None = None,
) -> AgentEvent:
    """§11.3 调用形态：emit(state.seq, state.phase, decision)。"""
    event = AgentEvent(
        seq=seq,
        session_id=session_id,
        run_id=run_id,
        ts=(now or datetime.now(UTC)).isoformat(),
        kind=kind or decision_to_kind(decision, None),  # type: ignore[arg-type]
        phase=str(phase),
        payload=payload if payload is not None else {"decision": decision.kind},
        trace_id=trace_id,
    )
    bus.emit(event)
    return event
