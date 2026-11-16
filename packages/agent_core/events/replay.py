"""M03 事件回放（§11.8 / §4.1 L23）：按 runId + seq 回放。

从注入的 EventBus（生产为 Redis/agent_event 表）取事件，按 seq 升序返回；
同一 runId+seq 重放两次结果完全一致（T20）。
"""
from __future__ import annotations

from packages.agent_core.injection import EventBus
from packages.agent_core.types import AgentEvent


def replay(bus: EventBus, run_id: str, from_seq: int) -> list[AgentEvent]:
    """返回该 run 中 seq >= from_seq 的事件，按 seq 升序。"""
    events = [e for e in bus.all() if e.run_id == run_id and e.seq >= from_seq]
    return sorted(events, key=lambda e: e.seq)


def replay_state(bus: EventBus, run_id: str) -> list[AgentEvent]:
    """完整回放一个 run（从 seq=0）。"""
    return replay(bus, run_id, 0)
