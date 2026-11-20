"""M05 事件发射（§3.2 step12 / §6.4 attachment 通道）。

事件是 M06/前端消费澄清与纠偏结果的唯一通道（A7/A8）。生产用 RedisEventBus
包装器（seq 由会话序号分配器单调分配），测试用内存 sink 可断言。
本模块不碰会话状态机——AWAITING_INPUT 仍由 M06 判定（v1.7 B2）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InMemoryEventSink:
    """内存事件 sink（测试桩）：记录全部 emit 的事件 dict。"""

    events: list[dict[str, object]] = field(default_factory=list)
    _seq: int = 0

    def emit(self, event: dict[str, object]) -> None:
        """记录事件并分配会话内单调 seq。"""
        self.events.append(dict(event))

    def next_seq(self, session_id: str = "") -> int:
        """分配下一个 seq（内存桩：单计数器；生产按 session 分桶）。"""
        self._seq += 1
        return self._seq


@dataclass(slots=True, frozen=True)
class AttachmentEvent:
    """附件事件载荷（kind ∈ {attachment, notice}，payload 为确定键 dict）。"""

    kind: str
    session_id: str
    payload: dict[str, object]
    seq: int = 0


class AttachmentEventEmitter:
    """把附件生命周期事件写入 sink（生产注入 RedisEventBus 适配器）。"""

    def __init__(self, sink: InMemoryEventSink) -> None:
        """注入事件 sink。"""
        self._sink = sink

    def emit_attachment(
        self, session_id: str, attachment_id: str, role: str | None, action: str
    ) -> None:
        """上传/纠偏完成事件（A8：新 seq > 旧 seq；best-effort 不拖垮主流程）。"""
        try:
            seq = self._sink.next_seq(session_id)
            self._sink.emit(
                {
                    "kind": "attachment",
                    "session_id": session_id,
                    "seq": seq,
                    "payload": {
                        "attachment_id": attachment_id,
                        "role": role,
                        "action": action,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - 事件是旁路，故障不影响已落库的主流程
            logger.warning("emit attachment event failed: %s", exc)

    def emit_notice(self, session_id: str, attachment_id: str, hint: str) -> None:
        """role 歧义澄清信号（A7：M06 消费，不置 AWAITING_INPUT；best-effort）。"""
        try:
            seq = self._sink.next_seq(session_id)
            self._sink.emit(
                {
                    "kind": "notice",
                    "session_id": session_id,
                    "seq": seq,
                    "payload": {
                        "attachment_id": attachment_id,
                        "clarify_hint": hint,
                        "role": None,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - 同上
            logger.warning("emit notice event failed: %s", exc)


class RedisEventSink:
    """生产事件 sink：把 M05 事件写入 M04 的 RedisEventBus（A8 跨 worker 可消费）。

    与 InMemoryEventSink 同接口（emit/next_seq）；seq 用 Redis INCR 分配，
    避免多 worker 各自从 1 开始造成 SSE 序号冲突。
    """

    def __init__(self, bus: object, redis_url: str) -> None:
        """注入 M04 RedisEventBus 与 Redis URL。"""
        self._bus = bus
        import redis

        self._redis = redis.from_url(redis_url)
        self._seq_prefix = "wp:m05:eventseq:"

    def emit(self, event: dict[str, object]) -> None:
        """把事件字典转成 AgentEvent 写入总线（best-effort 由 emitter 保证）。"""
        from datetime import UTC, datetime

        from packages.agent_core.types import AgentEvent

        self._bus.emit(
            AgentEvent(
                seq=int(event.get("seq", 0)),
                session_id=str(event.get("session_id", "")),
                run_id="",
                ts=datetime.now(UTC).isoformat(),
                kind=str(event.get("kind", "notice")),
                phase="",
                payload=event.get("payload", {}),  # type: ignore[arg-type]
                trace_id="",
            )
        )

    def next_seq(self, session_id: str = "") -> int:
        """Redis 原子自增分配**会话级**单调 seq（R-2：与 M04 会话 seq 同域）。

        M04 的 seq 是每会话 max+1；M05 若用全局计数器会写出比会话最大值小的
        seq，SSE 按 `seq > last_event_id` 续传时事件丢失。故必须 per-session。
        """
        key = f"{self._seq_prefix}{session_id or 'global'}"
        return int(self._redis.incr(key))
