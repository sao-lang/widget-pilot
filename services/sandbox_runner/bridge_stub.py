"""M08 bridge 桩判定（R11/I8）：能力调用必须按 manifest 放行，未声明拒绝并上报。

容器内 JS bridge 与线上 @widget/bridge 同源窄协议；本模块对容器上报的事件做
确定性复核，避免只信容器自报 success。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from packages.agent_core.types import JsonValue

__all__ = ["BridgeVerdict", "evaluate_bridge_events"]


@dataclass(slots=True, frozen=True)
class BridgeVerdict:
    """bridge 事件复核结论。"""

    denied: tuple[str, ...]
    widget_errors: int
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """未声明调用为 0 且无 widget:error 才通过。"""
        return not self.denied and self.widget_errors == 0


def evaluate_bridge_events(
    events: Sequence[Mapping[str, JsonValue]], declared: frozenset[str]
) -> BridgeVerdict:
    """逐条复核 capability_request 与 widget:error。"""
    denied: list[str] = []
    reasons: list[str] = []
    widget_errors = 0
    for event in events:
        kind = event.get("type")
        if kind == "capability_request":
            capability = event.get("capability")
            allowed = event.get("allowed")
            if not isinstance(capability, str) or capability not in declared or allowed is not True:
                name = capability if isinstance(capability, str) else "<invalid>"
                denied.append(name)
                reasons.append(f"CAPABILITY_DENIED:{name}")
        elif kind == "widget:error":
            widget_errors += 1
            code = event.get("code")
            reasons.append(f"widget:error:{code if isinstance(code, str) else '<unknown>'}")
    return BridgeVerdict(
        denied=tuple(denied), widget_errors=widget_errors, reasons=tuple(reasons)
    )
