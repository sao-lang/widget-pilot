"""M03 原生 SSE 帧与续传（§4.1 / §4.5）。

服务端单向、可回放、鉴权简单；显式 done/error（无 done 视为截断）；
Last-Event-ID 续传；X-Accel-Buffering: no（由服务层设 header）。
"""
from __future__ import annotations

import json

from packages.agent_core.injection import EventBus
from packages.agent_core.types import AgentEvent


def frame(event: AgentEvent) -> str:
    """构造一段 SSE 帧：id / event / data。"""
    data = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {data}\n\n"


def resume_from(bus: EventBus, session_id: str, last_event_id: int) -> list[AgentEvent]:
    """断线重连：从 Last-Event-ID+1 起按序补发，无重复无倒序。"""
    events = [e for e in bus.all() if e.session_id == session_id and e.seq > last_event_id]
    return sorted(events, key=lambda e: e.seq)
