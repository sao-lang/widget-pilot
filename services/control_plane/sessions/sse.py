"""M04 SSE 事件窗口与续传（§3.1 SSEGateway / §4.2 / §4.5）。

维护每会话最近 N 条事件（N 来自 M02 Q4 定稿），按 seq 单调递增；
Last-Event-ID 续传返回该 seq 之后的事件，超窗先拉快照（由上层处理）。
事件结构用 TypedDict 明确键（编码规则 4），禁裸 dict 拼装。
"""

from __future__ import annotations

from typing import TypedDict


class SSEEvent(TypedDict):
    """一条 SSE 事件的确定结构（§4.2 / §6.4）。"""

    seq: int
    """每会话单调递增的事件号（续传锚点）。"""
    kind: str
    """事件类型（AgentEventKind 值，此处以字符串承载便于 JSON 序列化）。"""
    payload: object
    """事件载荷（按 kind 收窄，JSON 兼容）。"""


class SSEWindow:
    """最近 N 条事件的滑窗 + 按 seq 续传（I5：无重复无倒序）。"""

    def __init__(self, window_n: int) -> None:
        """注入窗口大小（SSE_WINDOW_N，默认 500，M02 Q4 定稿）。"""
        self._window_n = window_n
        self._events: list[SSEEvent] = []

    def emit(self, seq: int, kind: str, payload: object) -> None:
        """追加一条事件并裁剪超出窗口的旧事件（保留最近 N 条）。"""
        self._events.append({"seq": seq, "kind": kind, "payload": payload})
        # 窗口裁剪：只保留最后 N 条，超窗的旧事件由上层拉快照补（§4.5）
        if len(self._events) > self._window_n:
            self._events = self._events[-self._window_n :]

    def since(self, last_event_id: int) -> list[SSEEvent]:
        """返回 seq > last_event_id 的事件（A11 续传：从下一条按序补发）。"""
        return [e for e in self._events if e["seq"] > last_event_id]

    def all(self) -> list[SSEEvent]:
        """返回当前窗口内全部事件（副本，防外部修改内部状态）。"""
        return list(self._events)

    def last_seq(self) -> int:
        """返回窗口内最大 seq，空窗返回 -1（供快照 last_seq 锚点）。"""
        return self._events[-1]["seq"] if self._events else -1
