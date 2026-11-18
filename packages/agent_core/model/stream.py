"""M03 SSE 分帧与半帧缓存（§11.2 / §11.7）。

按 \\n\\n 分帧并缓存半帧，UTF-8 流式解码；delta 解析；[DONE] 显式结束。
"""
from __future__ import annotations

import json

from packages.agent_core.types import Delta, StreamBuffer


def _parse_frame(frame: str) -> list[Delta]:
    """解析一帧 SSE data 行。"""
    out: list[Delta] = []
    # 逐行扫描帧内容：SSE 的负载只在 data: 行上，其余字段行需跳过
    for line in frame.splitlines():
        # 非 data 行（如 event/id/retry）不携带 delta，直接忽略
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        # [DONE] 是流结束哨兵，转成 done delta 通知下游终止
        if payload == "[DONE]":
            out.append(Delta(done=True))
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            out.append(Delta(text=payload))
            continue
        # 非对象 JSON（数组/标量）不符合帧协议，丢弃而非误当 delta
        if not isinstance(obj, dict):
            continue
        # 简化帧：{"delta":"text"} 或 {"text":"..."}
        if "delta" in obj and isinstance(obj["delta"], str):
            out.append(Delta(text=obj["delta"]))
            continue
        # OpenAI 帧：{"choices":[{"delta":{"content":"..."}}]}
        choices = obj.get("choices") or [{}]
        delta = (choices[0].get("delta") or {}) if isinstance(choices[0], dict) else {}
        text = delta.get("content", "") if isinstance(delta, dict) else ""
        # 仅当取到非空文本才产出 delta，避免空帧产生无意义事件
        if text:
            out.append(Delta(text=text))
    return out


def parse(chunk: bytes, buf: StreamBuffer, encoding: str = "utf-8") -> list[Delta]:
    """§4.1 L2：把 chunk 并入缓存，按 \\n\\n 切帧解析，半帧留缓存。"""
    text = chunk.decode(encoding, errors="replace")
    combined = buf.text + text
    parts = combined.split("\n\n")
    buf.text = parts.pop()  # 最后一段可能是半帧，缓存
    deltas: list[Delta] = []
    # 只解析切出的完整帧；最后一段半帧已留缓存，等下次 chunk 拼齐
    for frame in parts:
        # 跳过纯空白帧，避免把分隔符残留当成有效负载
        if frame.strip():
            d = _parse_frame(frame)
            deltas.extend(d)
            buf.deltas.extend(d)
    return deltas


class StreamDecoder:
    """有状态分帧器（语义同 parse，封装内部 buffer）。"""

    def __init__(self) -> None:
        self._buf = StreamBuffer()

    def feed(self, chunk: bytes) -> list[Delta]:
        """把新到字节块交给有状态分帧器，返回该块产出的增量事件。"""
        return parse(chunk, self._buf)

    @property
    def buffered(self) -> str:
        """返回尚未凑成完整帧、留在缓存中的文本。"""
        return self._buf.text
