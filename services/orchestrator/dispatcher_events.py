"""P1 #42：确定性命中分支的终态事件发射器工厂。

命中确定性通道跳过 run()（内部发 checkpoint/cost/done），本工厂产出的闭包
补发终态/澄清事件，seq 与 run.py 同源（从事件窗口 next_seq），payload 与
run.py:422-425 的 done/error 一致。生产由 worker 注入，测试可直接调闭包断言。
"""
from __future__ import annotations

from typing import cast

from packages.agent_core.events import seq as events_seq
from packages.agent_core.injection import Deps
from packages.agent_core.types import (
    Decision,
    DecisionKind,
    JsonObject,
    State,
    TerminalKind,
)
from packages.contracts.enums import AgentEventKind

__all__ = ["make_terminal_event_emitter"]


def make_terminal_event_emitter(deps: Deps):
    """构造 ``Callable[[State], None]``：按 state 发 DONE/ERROR/ASK_USER 事件。"""
    def emit(state: State) -> None:
        seq = events_seq.next_seq(deps.event_bus.all(), state.session_id)
        terminal = state.terminal
        payload: JsonObject
        if terminal is not None:
            kind = (
                AgentEventKind.DONE
                if terminal.kind is TerminalKind.DONE
                else AgentEventKind.ERROR
            )
            # snapshot()["terminal"] 含 None 值，严格类型非 JsonValue；与
            # run.py:425 同款 cast 收窄（结构已由 Terminal 不变量保证）。
            payload = cast(JsonObject, {"terminal": state.snapshot()["terminal"]})
        else:
            kind = AgentEventKind.ASK_USER
            # NB2（review Round2）：State.pause 不写 spec.reason，精准反问载荷
            # （reason + choices 五字段）由 dispatcher 存 contextvar；SSE 必须
            # 带出，否则前端拿不到候选、只能死循环追问。
            import json

            from services.orchestrator.request_context import clarification_payload_var

            raw = clarification_payload_var.get()
            parsed = json.loads(raw) if raw else {}
            question = str(parsed.get("reason") or state.spec.reason or "需要补充信息")
            payload = cast(JsonObject, {
                "question": question,
                "question_kind": str(parsed.get("question_kind") or ""),
                "choices": parsed.get("choices") or [],
                "missing_slots": parsed.get("missing_slots") or [],
                "evidence_ref": raw,
            })
        events_seq.emit(
            seq, state.phase, Decision(DecisionKind.DONE),
            bus=deps.event_bus, session_id=state.session_id, run_id=state.run_id,
            trace_id=f"trace-det-{state.run_id}", payload=payload,
            now=deps.clock.now(), kind=kind.value,
        )

    return emit
