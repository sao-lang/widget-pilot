"""能力闸门生产适配器（ADR-003 / B2）——把 M06 确定性校验接到 agent loop。

实现 agent_core.injection.CapabilityGuard 鸭子协议；内核不 import 上层，由
build_deps 注入本适配器，完成「任何 L2 请求都过闸」。
"""
from __future__ import annotations

from services.guardrails.capability_guard import check_capability_request
from services.guardrails.types import CapabilityRequest, TurnState


class ProductionCapabilityGuard:
    """生产能力闸门：委托 M06 check_capability_request 五项校验。"""

    def check(
        self, capability_id: str, level: int, surface: str, count: int, tokens: int
    ) -> tuple[bool, str | None]:
        """按 (id, level, surface, 已用次数, 已用 token) 判定放行。

        Args:
            capability_id: 能力句柄。
            level: 披露层级（1/2）。
            surface: 调用表面。
            count: 本轮已加载次数。
            tokens: 本轮已加载 token（本次成本在 check 内按 0 计，成本由调用后累加）。

        Returns:
            (是否放行, 拒绝原因)；拒绝原因可直接作 ReasonCode 归因。
        """
        from packages.agent_core.tools.registry import REGISTRY

        result = check_capability_request(
            CapabilityRequest(capability_id, level, surface),
            TurnState(capability_count=count, capability_tokens=tokens),
            frozenset(REGISTRY.keys()),
            token_cost=0,
        )
        return result.ok, result.reason
