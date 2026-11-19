"""M04 SSE 事件窗口与续传（§3.1 SSEGateway / §4.2 / §4.5）。

维护每会话最近 N 条事件（N 来自 M02 Q4 定稿），按 seq 单调递增；
Last-Event-ID 续传返回该 seq 之后的事件，超窗先拉快照（由上层处理）。
事件结构用 TypedDict 明确键（编码规则 4），禁裸 dict 拼装。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypedDict, runtime_checkable


class SSEEvent(TypedDict):
    """一条 SSE 事件的确定结构（§4.2 / §6.4）。"""

    seq: int
    """每会话单调递增的事件号（续传锚点）。"""
    kind: str
    """事件类型（AgentEventKind 值，此处以字符串承载便于 JSON 序列化）。"""
    payload: object
    """事件载荷（按 kind 收窄，JSON 兼容）。"""


@runtime_checkable
class EventWindow(Protocol):
    """SSE 事件源协议（规则 8/9：内存窗口/Redis 流双实现可插拔）。"""

    def since(
        self, last_event_id: int, session_id: str | None = None
    ) -> list[SSEEvent]:
        """返回 seq > last_event_id 的事件（按 seq 升序，I5）。

        session_id：Redis 实现按会话拉流所需；内存窗口忽略。
        """
        ...


class SSEWindow:
    """最近 N 条事件的滑窗 + 按 seq 续传（I5：无重复无倒序）。"""

    def __init__(self, window_n: int) -> None:
        """注入窗口大小（SSE_WINDOW_N，默认 500，M02 Q4 定稿）。"""
        self._window_n = window_n
        self._events: list[SSEEvent] = []

    def emit(self, seq: int, kind: str, payload: object) -> None:
        """追加一条事件并裁剪超出窗口的旧事件（保留最近 N 条）。

        Args:
            seq: 会话内递增序号（由调用方保证连续、无重复）。
            kind: 事件类型（AgentEventKind 值）。
            payload: 事件载荷，JSON 兼容。

        超窗的旧事件由上层拉快照补（§4.5），这里直接丢弃不占内存。
        """
        self._events.append({"seq": seq, "kind": kind, "payload": payload})
        # 窗口裁剪：只保留最后 N 条，超窗的旧事件由上层拉快照补（§4.5）
        if len(self._events) > self._window_n:
            self._events = self._events[-self._window_n :]

    def since(
        self, last_event_id: int, session_id: str | None = None
    ) -> list[SSEEvent]:
        """返回 seq > last_event_id 的事件（A11 续传：从下一条按序补发）。

        内存窗口全局共享，session_id 参数仅用于对齐 EventWindow 协议。
        """
        return [e for e in self._events if e["seq"] > last_event_id]

    def all(self) -> list[SSEEvent]:
        """返回当前窗口内全部事件（副本，防外部修改内部状态）。

        Returns:
            按 seq 升序的事件列表拷贝。
        """
        return list(self._events)

    def last_seq(self) -> int:
        """返回窗口内最大 seq，空窗返回 -1（供快照 last_seq 锚点）。

        Returns:
            最大 seq；空窗 -1（与 §6.1 Session.last_seq 初始 0 区分：无事件即无锚点）。
        """
        return self._events[-1]["seq"] if self._events else -1


class RedisEventSource:
    """EventWindow 生产实现：把 RedisEventBus 的 AgentEvent 流适配为 SSEEvent。

    注入 window_of(session_id, n) 回调（即 RedisEventBus.window），不重实现
    Redis/schema 还原（规则 8）。session_id 由 SSE 端点按请求传入。
    """

    def __init__(
        self,
        window_of: Callable[[str, int], Sequence[object]],
        window_n: int = 500,
    ) -> None:
        """注入事件窗口回调与保留窗口大小（SSE_WINDOW_N）。"""
        self._window_of = window_of
        self._window_n = window_n

    def since(
        self, last_event_id: int, session_id: str | None = None
    ) -> list[SSEEvent]:
        """从 Redis 拉该会话窗口事件，转 SSE 视图，按 seq 过滤（I5）。

        Args:
            last_event_id: 客户端上次收到的 seq；只返回 seq > 此值的事件。
            session_id: 会话标识，定位 Redis Stream key（缺失则返回空）。

        Returns:
            按 seq 升序的 SSEEvent 列表（只含 seq/kind/payload 三字段）。
        """
        if not session_id:
            return []  # 无 session 无法定位 Redis Stream key
        result: list[SSEEvent] = []
        for event in self._window_of(session_id, self._window_n):
            seq = getattr(event, "seq", 0)
            if seq > last_event_id:
                result.append(
                    {
                        "seq": seq,
                        "kind": str(getattr(event, "kind", "")),
                        "payload": getattr(event, "payload", {}),
                    }
                )
        return result
