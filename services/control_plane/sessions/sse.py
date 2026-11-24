"""M04 SSE 事件窗口协议与 Redis 生产实现（§3.1 SSEGateway / §4.2 / §4.5）。

事件结构用 TypedDict 明确键（编码规则 4），禁裸 dict 拼装。
内存窗口实现只存在于 `tests/support/control_plane.py`；生产走 Redis Streams。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypedDict, runtime_checkable

from packages.agent_core.types import AgentEvent, JsonValue


class SSEEvent(TypedDict):
    """一条 SSE 事件的确定结构（§4.2 / §6.4）。"""

    seq: int
    """每会话单调递增的事件号（续传锚点）。"""
    kind: str
    """事件类型（AgentEventKind 值，此处以字符串承载便于 JSON 序列化）。"""
    payload: JsonValue
    """事件载荷（按 kind 收窄，JSON 兼容）。"""


@runtime_checkable
class EventWindow(Protocol):
    """SSE 事件源协议（规则 8/9：Redis 生产与测试替身可插拔）。"""

    def since(
        self, last_event_id: int, session_id: str | None = None
    ) -> list[SSEEvent]:
        """返回 seq > last_event_id 的事件（按 seq 升序，I5）。

        session_id：Redis 实现按会话拉流所需；测试替身可忽略。
        """
        ...


class RedisEventSource:
    """EventWindow 生产实现：把 RedisEventBus 的 AgentEvent 流适配为 SSEEvent。

    注入 window_of(session_id, n) 回调（即 RedisEventBus.window），不重实现
    Redis/schema 还原（规则 8）。session_id 由 SSE 端点按请求传入。

    Attributes:
        _window_of: (session_id, n) → AgentEvent 序列的生产回调。
        _window_n: 保留窗口大小（SSE_WINDOW_N）。
    """

    def __init__(
        self,
        window_of: Callable[[str, int], Sequence[AgentEvent]],
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


@runtime_checkable
class EventTailPort(Protocol):
    """SSE 实时尾随接线端口（ADR-008 reasoning 流式可视化的 M04 侧契约）。

    【本模块只留接线，不实现内部逻辑】浏览器展示模型思考过程属 ADR-008 方案，
    当前阶段不实现（前端折叠组件 + XREAD BLOCK tail + frames 实时推送均未做）。
    本端口仅为后续实现预留可插拔锚点：届时注入 Redis XREAD BLOCK 实现，SSE 端点
    据此把有限窗口一次性下发改为长连接实时推送。未注入时端点保持既有有限窗口
    行为（§4.5 客户端凭 Last-Event-ID 续传），不降级为任何内存桩。
    """

    def tail(
        self, last_id: str, session_id: str, timeout_ms: int
    ) -> list[tuple[str, SSEEvent]]:
        """阻塞等一批新事件（或超时），返回 [(stream_id, event), ...] 按序。

        Args:
            last_id: Redis Stream 上次读到的 id（'$' 表示只等新事件）。
            session_id: 会话标识，定位 Stream key。
            timeout_ms: XREAD BLOCK 毫秒（心跳间隔控制）。

        Returns:
            新事件列表（空表示超时无新事件）；stream_id 用于下次续传锚点。
        """
        ...

