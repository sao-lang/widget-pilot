"""M09 → M04 SSE 事件发射的生产适配（artifact/notice/error 经 Redis 扇出）。

BundleService 的 EventSinkPort 生产实现：按 session_id 定位统一 seq 域，
用 TypedEventEmitter 落 RedisEventBus，M04 SSE 端点消费。无内存桩、无 fake。
"""
from __future__ import annotations

from packages.agent_core.injection import EventBus
from packages.contracts.types import JsonObject
from services.control_plane.bundles.ports import EventSinkPort
from services.orchestrator.tasks.events import RedisSeqAllocator, TypedEventEmitter


class M04ArtifactSink:
    """生产事件 sink（每次 emit 按 session_id 构造 TypedEventEmitter）。"""

    def __init__(self, bus: EventBus, allocator: RedisSeqAllocator) -> None:
        """注入真实事件总线与原子 seq 分配器。"""
        self._bus = bus
        self._allocator = allocator

    def emit(self, kind: str, payload: JsonObject, session_id: str = "") -> None:
        """发出一条事件；run_id 取 payload.bundle_id（产物归因），缺则空串。"""
        run_id = str(payload.get("bundle_id", ""))
        emitter = TypedEventEmitter(
            bus=self._bus,
            allocator=self._allocator,
            session_id=session_id,
            run_id=run_id,
        )
        emitter.emit(kind, payload, phase="COMPLETED" if kind == "artifact" else "RUNNING")


def make_artifact_sink(bus: EventBus, redis_url: str) -> EventSinkPort:
    """生产工厂：构造 M04 事件 sink（含 Redis 原子 seq 分配器）。"""
    return M04ArtifactSink(bus, RedisSeqAllocator(redis_url))
