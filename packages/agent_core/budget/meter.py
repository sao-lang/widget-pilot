"""M03 四维成本硬上限与按档位分账（§11.8 / §4.1 L12/L13）。

每 super-step 累加 token / 轮次 / 工具调用 / 沙箱分钟；触顶由 check() 抛
CoreError(BUDGET_EXHAUSTED) 确定性中断——「不提示模型省着点」（§1.7 #5）。

类型改造后：用量字典为 :class:`BudgetUsage`（确定键 TypedDict），不再裸 ``dict``；
``add`` 的 ``**delta`` 用 :class:`BudgetDelta` 约束可累加维度。
"""
from __future__ import annotations

from typing import TypedDict, Unpack

from packages.agent_core.constants import BUDGET_DIMENSIONS, DEFAULT_BUDGET_LIMITS
from packages.agent_core.errors import CoreError
from packages.agent_core.types import BudgetLimits, State, Usage


class BudgetDelta(TypedDict, total=False):
    """``meter.add`` 可直接累加的维度（tokens 由 usage 归一，不在此列）。"""

    iterations: float
    """额外轮次。"""
    tool_calls: float
    """工具调用次数。"""
    sandbox_minutes: float
    """沙箱分钟数。"""


def add(
    state: State,
    usage: Usage | None = None,
    tier: str | None = None,
    **delta: Unpack[BudgetDelta],
) -> State:
    """累加四维用量，返回新 State（不原地改）。

    tokens 只能来自归一后的 usage；其余维度经 ``delta`` 传入且必须在
    :class:`BudgetDelta` 声明的键集合内。
    """
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


def check(state: State, limits: BudgetLimits | None = None) -> None:
    """四维硬上限；任一维触顶 → 确定性中断（CoreError）。

    limits 覆盖时使用调用方提供的 :class:`BudgetLimits`，否则用
    :data:`DEFAULT_BUDGET_LIMITS`（同为确定键结构）。
    """
    lim = limits or DEFAULT_BUDGET_LIMITS
    used = state.budget_used
    for dim in BUDGET_DIMENSIONS:
        cap = lim.get(dim)
        if cap is not None and used.get(dim, 0) >= cap:
            raise CoreError.business(
                "BUDGET_EXHAUSTED",
                f"budget dimension {dim} exhausted: {used.get(dim, 0)} >= {cap}",
            )
