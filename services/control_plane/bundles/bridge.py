"""M09 窄 bridge 协议与能力放行（§4.3.3 / I5/I8/A8/A9/A18）。

宿主 ↔ 控件只走 7 条固定消息，非任意 JSON-RPC；未声明能力调用拒绝并上报
widget:error。时间统一 host:tick、存储按 storage_namespace 隔离——控件不得
自取系统时间或跨 namespace 读写。
"""
from __future__ import annotations

from packages.contracts.enums import Capability

BRIDGE_MESSAGES: tuple[str, ...] = (
    "widget:ready",
    "widget:resize",
    "widget:error",
    "host:theme",
    "host:locale",
    "host:tick",
    "host:data",
)
"""7 条窄消息定值（§6.3，编码规则 5：集合常量具名）。"""

GLOBAL_CAPABILITY_ALLOWLIST: frozenset[Capability] = frozenset(Capability)
"""全局能力白名单全集（§6.3 Q5：默认 = Capability 全集，策略可收窄注入）。"""


def assert_message_allowed(kind: str) -> bool:
    """判定消息名是否在 7 条白名单内（A8：未知消息名拒绝）。"""
    return kind in BRIDGE_MESSAGES


def is_capability_allowed(
    capability: Capability, capability_bridge: set[Capability] | frozenset[Capability]
) -> bool:
    """判定能力调用是否被运行时放行（A9：未声明能力拒绝）。"""
    return capability in capability_bridge
