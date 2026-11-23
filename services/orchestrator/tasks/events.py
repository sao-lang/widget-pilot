"""ADR-006 §6.2 统一 seq 域 + 类型化事件发射（生产真实 Redis 原子 INCR）。

事件 seq 由 ``wp:eventseq:{session_id}`` 的 Redis INCR 统一分配，M04 loop、
M05 sink、Coordinator、子任务共用这一域；首次用 max(session.last_seq, 事件窗口
最大 seq) 一次性 seed。``next_seq``（事件重算）降级为 seed 兜底，不再作运行时
分配器——解决并发写撞号（原 next_seq 从 bus.all() 取 max+1，并发必然撞）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

import redis

from packages.agent_core.events import seq as events_seq
from packages.agent_core.injection import EventBus
from packages.agent_core.types import AgentEvent, JsonObject

logger = logging.getLogger(__name__)

SEQ_KEY_PREFIX = "wp:eventseq:"
"""统一 seq 域前缀（每 session 一个原子计数器）。"""


class RedisSeqAllocator:
    """Redis 原子 seq 分配器（生产真实实现；无 fake 降级）。"""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: redis.Redis | None = None

    def _conn(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self._url)
        return self._client

    def seed(self, session_id: str, value: int) -> None:
        """一次性 seed：仅在计数器不存在时写入（SETNX 语义，不覆盖后续增长）。"""
        conn = self._conn()
        key = SEQ_KEY_PREFIX + session_id
        if conn.get(key) is None:
            conn.setnx(key, int(value))

    def next(self, session_id: str) -> int:
        """原子增一并返回新 seq（0-based 与既有 next_seq 对齐）。"""
        value = int(self._conn().incr(SEQ_KEY_PREFIX + session_id)) - 1
        return max(value, 0)


class TypedEventEmitter:
    """事件发射器（经 event_bus 落 Redis Stream，带可选任务/轮次归因字段）。"""

    def __init__(
        self,
        bus: EventBus,
        allocator: RedisSeqAllocator,
        session_id: str,
        run_id: str,
        trace_id: str = "",
    ) -> None:
        self._bus = bus
        self._allocator = allocator
        self._session_id = session_id
        self._run_id = run_id
        self._trace_id = trace_id or str(uuid4())

    def emit(
        self,
        kind: str,
        payload: JsonObject,
        *,
        task_id: str | None = None,
        task_index: int | None = None,
        turn: int | None = None,
        phase: str = "RUNNING",
    ) -> None:
        """发出一条带可选 task/turn 归因的事件（N=1 调用方不传这些字段）。"""
        seq = self._allocator.next(self._session_id)
        event = AgentEvent(
            seq=seq,
            session_id=self._session_id,
            run_id=self._run_id,
            ts=datetime.now(UTC).isoformat(),
            kind=kind,
            phase=phase,
            payload=payload,
            trace_id=self._trace_id,
            task_id=task_id,
            task_index=task_index,
            turn=turn,
        )
        self._bus.emit(event)


def seed_from_window(allocator: RedisSeqAllocator, bus: EventBus, session_id: str, last_seq: int) -> None:
    """用 session.last_seq 与事件窗口最大值一次性 seed（防 seq 回退）。"""
    window_max = events_seq.next_seq(bus.window(session_id, 500), session_id)
    allocator.seed(session_id, max(last_seq, window_max))


__all__ = ["SEQ_KEY_PREFIX", "RedisSeqAllocator", "TypedEventEmitter", "seed_from_window"]
