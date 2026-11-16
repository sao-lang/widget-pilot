"""M03 events 层：T18 seq 连续 / T20 重放一致 / 断线重连 Last-Event-ID。"""
from __future__ import annotations

from datetime import UTC, datetime

from packages.agent_core.events.replay import replay
from packages.agent_core.events.seq import emit, next_seq
from packages.agent_core.events.sse import frame, resume_from
from packages.agent_core.types import Decision


def _emit_n(bus, session, run, n, trace="t1"):
    d = Decision("CALL_MODEL")
    for i in range(n):
        emit(i, "RUNNING", d, bus=bus, session_id=session, run_id=run,
             trace_id=trace, now=datetime(2026, 1, 1, tzinfo=UTC))


# ---- T18：seq 连续无重复无倒序 ----
def test_T18_seq_continuous(event_bus):
    _emit_n(event_bus, "s1", "run1", 20)
    seqs = [e.seq for e in event_bus.all()]
    assert seqs == list(range(20))
    assert next_seq(event_bus.all(), "s1") == 20
    assert next_seq(event_bus.all(), "s-other") == 0


# ---- T20：replay 两次结果完全一致 ----
def test_T20_replay_twice_identical(event_bus):
    _emit_n(event_bus, "s1", "run1", 5)
    r1 = replay(event_bus, "run1", 0)
    r2 = replay(event_bus, "run1", 0)
    assert [e.to_dict() for e in r1] == [e.to_dict() for e in r2]
    assert len(r1) == 5


def test_T20_replay_from_seq(event_bus):
    _emit_n(event_bus, "s1", "run1", 5)
    r = replay(event_bus, "run1", 3)
    assert [e.seq for e in r] == [3, 4]


# ---- SSE 帧 ----
def test_sse_frame_format():
    from packages.agent_core.types import AgentEvent
    e = AgentEvent(1, "s", "r", "2026-01-01T00:00:00+00:00", "done", "COMPLETED", {"x": 1}, "t")
    f = frame(e)
    assert f.startswith("id: 1\n")
    assert "event: done\n" in f
    assert "data: " in f


# ---- 断线重连：Last-Event-ID=17 → 从 18 起补发，无重复无倒序 ----
def test_reconnect_from_last_event_id(event_bus):
    _emit_n(event_bus, "s1", "run1", 25)
    missed = resume_from(event_bus, "s1", last_event_id=17)
    seqs = [e.seq for e in missed]
    assert seqs == list(range(18, 25))
    assert all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1))


def test_reconnect_beyond_window_returns_empty(event_bus):
    _emit_n(event_bus, "s1", "run1", 10)
    assert resume_from(event_bus, "s1", last_event_id=99) == []
