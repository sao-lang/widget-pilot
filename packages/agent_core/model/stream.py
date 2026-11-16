"""M03 SSE 分帧与半帧缓存（§11.2 / §11.7）。

按 \\n\\n 分帧并缓存半帧，UTF-8 流式解码；delta 解析；[DONE] 显式结束。
"""
from __future__ import annotations

import json

from packages.agent_core.types import Delta, StreamBuffer


def _parse_frame(frame: str) -> list[Delta]:
    """解析一帧 SSE data 行。"""
    out: list[Delta] = []
    for line in frame.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            out.append(Delta(done=True))
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            out.append(Delta(text=payload))
            continue
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
    for frame in parts:
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
        return parse(chunk, self._buf)

    @property
    def buffered(self) -> str:
        return self._buf.text
