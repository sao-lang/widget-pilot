"""M03 工具静态注册（§11.6 / §4.1 L8/L9）。

REGISTRY 启动时静态注册，运行时不可增删（§7.3 #1）。register 仅启动期可用；
freeze_registry() 后拒绝任何变更。
"""
from __future__ import annotations

from packages.agent_core.errors import CoreError
from packages.agent_core.types import ToolSpec
from packages.contracts.enums import ReasonCode

REGISTRY: dict[str, ToolSpec] = {}
_FROZEN = False


def register(name: str, spec: ToolSpec) -> None:
    """启动期注册入口。冻结后调用抛 CoreError（运行时不可增删）。"""
    if _FROZEN:
        raise CoreError.business(ReasonCode.CAPABILITY_DENIED, "REGISTRY frozen: runtime mutation forbidden")
    REGISTRY[name] = spec


def freeze_registry() -> None:
    """冻结注册表，此后 register 一律拒绝，保证运行时拓扑固定（§7.3 #1）。"""
    global _FROZEN
    _FROZEN = True


def unfreeze_registry() -> None:
    """仅供测试解封；生产永不调用。"""
    global _FROZEN
    _FROZEN = False


def is_frozen() -> bool:
    """返回注册表当前是否已冻结，供装配期与测试自检状态。"""
    return _FROZEN
