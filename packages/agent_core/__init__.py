"""WidgetPilot 手写 Harness 内核（M03 / 原 §11）。

D-5「零 Agent 框架」定稿的库级载体：把状态迁移、幂等、恢复、结构化校验、
成本计量显式化为可单测、可断言的代码。本包无独立进程、无端口、不落库表、
不提供 REST；所有跨进程依赖以 Protocol 注入（见 injection.py）。
"""
from __future__ import annotations

from packages.agent_core.errors import CoreError, classify

__all__ = ["CoreError", "classify"]
