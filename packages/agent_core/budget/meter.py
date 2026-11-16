"""M03 四维成本硬上限与按档位分账（§11.8 / §4.1 L12/L13）。

每 super-step 累加 token / 轮次 / 工具调用 / 沙箱分钟；触顶由 check() 抛
CoreError(BUDGET_EXHAUSTED) 确定性中断——「不提示模型省着点」（§1.7 #5）。
"""
from __future__ import annotations

from packages.agent_core.constants import BUDGET_DIMENSIONS, DEFAULT_BUDGET_LIMITS
from packages.agent_core.errors import CoreError
from packages.agent_core.types import State, Usage


def add(state: State, usage: Usage | None = None, tier: str | None = None, **delta: float) -> State:
    """累加四维用量，返回新 State（不原地改）。"""
    used = dict(state.budget_used)
    if usage is not None:
        used["tokens"] = used.get("tokens", 0) + usage.total_tokens
    used["iterations"] = used.get("iterations", 0) + 1
    for k, v in delta.items():
        if k in BUDGET_DIMENSIONS:
            used[k] = used.get(k, 0) + v
    history = list(state.tier_history)
    if tier is not None:
        history.append(tier)
    return state.bump(budget_used=used, tier_history=history)


def check(state: State, limits: dict[str, float] | None = None) -> None:
    """四维硬上限；任一维触顶 → 确定性中断（CoreError）。"""
    lim = limits or DEFAULT_BUDGET_LIMITS
    used = state.budget_used
    for dim in BUDGET_DIMENSIONS:
        cap = lim.get(dim)
        if cap is not None and used.get(dim, 0) >= cap:
            raise CoreError.business(
                "BUDGET_EXHAUSTED",
                f"budget dimension {dim} exhausted: {used.get(dim, 0)} >= {cap}",
            )
