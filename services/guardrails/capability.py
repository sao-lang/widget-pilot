"""M08 能力白名单（R2/R11）：声明校验与 bridge 放行判定。

未声明 / 非 Capability 枚举值一律 False，触发 CAPABILITY_DENIED + widget:error；
零模型调用、零外部依赖。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from packages.agent_core.types import JsonValue
from packages.contracts.enums import Capability

__all__ = [
    "CAPABILITY_VALUES",
    "assert_capability_allowed",
    "declared_capabilities",
    "invalid_capabilities",
]

CAPABILITY_VALUES: frozenset[str] = frozenset(cap.value for cap in Capability)


def assert_capability_allowed(capability: str, declared: set[str]) -> bool:
    """能力未在 manifest 声明或不在全局白名单 → False。"""
    return capability in declared and capability in CAPABILITY_VALUES


def declared_capabilities(spec_or_manifest: Mapping[str, JsonValue]) -> frozenset[str]:
    """从 spec/manifest 读取 capabilities（非字符串值忽略，交给 schema 判据拦）。"""
    raw = spec_or_manifest.get("capabilities", ())
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(value for value in raw if isinstance(value, str))


def invalid_capabilities(capabilities: Iterable[JsonValue]) -> tuple[str, ...]:
    """枚举外的能力名（只返回字符串名，不返回任意对象）。"""
    return tuple(
        value
        for value in capabilities
        if isinstance(value, str) and value not in CAPABILITY_VALUES
    )
