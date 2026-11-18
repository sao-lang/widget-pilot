"""M03 四维成本硬上限与按档位分账（§11.8 / §4.1 L12/L13）。

每 super-step 累加 token / 轮次 / 工具调用 / 沙箱分钟；触顶由 check() 抛
CoreError(BUDGET_EXHAUSTED) 确定性中断——「不提示模型省着点」（§1.7 #5）。

类型改造后：用量字典为 :class:`BudgetUsage`（确定键 TypedDict），不再裸 ``dict``；
``add`` 的 ``**delta`` 用 :class:`BudgetDelta` 约束可累加维度。
"""
from __future__ import annotations

from typing import TypedDict, Unpack, cast

from packages.agent_core.constants import BUDGET_DIMENSIONS, DEFAULT_BUDGET_LIMITS
from packages.agent_core.errors import CoreError
from packages.agent_core.types import BudgetLimits, BudgetUsage, State, Usage
from packages.contracts.enums import ReasonCode


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
    # cast 到可变数值字典：BudgetUsage 是确定键 TypedDict，逐维累加需按变量键写入，
    # 用 dict[str, float] 视图运算后再 cast 回，避免 object 污染（规则 3/4）
    used = cast(dict[str, float], dict(state.budget_used))
    if usage is not None:
        used["tokens"] = used.get("tokens", 0.0) + usage.total_tokens
    used["iterations"] = used.get("iterations", 0.0) + 1
    for k, v in delta.items():
        # 只处理白名单维度，忽略未知键以保持 add 的契约稳定
        if k in BUDGET_DIMENSIONS:
            # Unpack[BudgetDelta].items() 的值被推断为 object，cast 到 float 再累加
            used[k] = used.get(k, 0.0) + cast(float, v)
    history = list(state.tier_history)
    if tier is not None:
        history.append(tier)
    return state.bump(budget_used=cast(BudgetUsage, used), tier_history=history)


def check(state: State, limits: BudgetLimits | None = None) -> None:
    """四维硬上限；任一维触顶 → 确定性中断（CoreError）。

    limits 覆盖时使用调用方提供的 :class:`BudgetLimits`，否则用
    :data:`DEFAULT_BUDGET_LIMITS`（同为确定键结构）。
    """
    lim = limits or DEFAULT_BUDGET_LIMITS
    used = state.budget_used
    for dim in BUDGET_DIMENSIONS:
        # 逐维检查四个硬上限，任一维触顶即确定性中断
        cap = lim.get(dim)
        if cap is not None and used.get(dim, 0) >= cap:
            # 仅当该维配置了上限且用量已达/超才触发，缺省 None 视为无限制
            raise CoreError.business(
                ReasonCode.BUDGET_EXHAUSTED,
                f"budget dimension {dim} exhausted: {used.get(dim, 0)} >= {cap}",
            )
