"""M03 上下文 token 计数 / 压缩 / 窗口化（§11.7 / §4.1）。

tiktoken 仅作纯计数工具库；超阈值只压缩 tool message，**保留任务契约与
已排除方案（excluded_approaches）**（X2 不变量）。
"""
from __future__ import annotations

from packages.agent_core.types import Message, State


def count_tokens(text: str) -> int:
    """tiktoken 纯计数；不可用时退化为字符数估算（不影响控制流）。"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001 - tiktoken 计数失败降级为估算，观测不影响控制流
        return len(text) // 4 + 1


def compress_tool_messages(state: State, max_chars: int = 2000) -> str:
    """压缩 tool message 文本，但保留 excluded_approaches（不丢已排除方案）。"""
    preserved = "；已排除方案：" + "、".join(state.excluded_approaches) if state.excluded_approaches else ""
    spec_text = str(state.spec or {})
    budget = max(0, max_chars - len(preserved))
    clipped = spec_text[:budget]
    return clipped + preserved


def assemble_messages(state: State) -> list[Message]:
    """默认槽位装配（context/slots.assemble 的便捷包装，避免循环依赖）。"""
    from packages.agent_core.context.slots import assemble
    from packages.agent_core.types import SlotConfig
    return assemble(state, SlotConfig())
