"""成本计量适配器：把 M03 ``meter`` 的累计值落成一条成本账（B7）。

``packages/agent_core/budget/meter.py`` 只负责四维累加（库级实现），
「什么算一轮、按什么档位分账、账本落到哪里」是 M07 的业务规则——本模块
把 ``State.budget_used`` / ``state.tier_history`` 归一成 :class:`CostEntry`，
算出复现指纹后调用账本端口追加。**纯函数式：不修改传入的 State**。
"""
from __future__ import annotations

from typing import Final

from packages.agent_core.injection import Deps
from packages.agent_core.types import State
from services.orchestrator.cost.ports import CostEntry, CostEntryMeta, CostLedgerPort
from services.orchestrator.memory.repro import compute_repro_fingerprint

_NO_TIER: Final[str] = "unknown"
"""档位历史为空时的兜底档位名（具名常量，非散落魔法串）。"""


def accrue(
    state: State,
    deps: Deps,
    ledger: CostLedgerPort,
    entry_meta: CostEntryMeta,
) -> None:
    """从 State 累计值构造 CostEntry 并落账；不改 State。

    四维用量直接取 ``state.budget_used``（已由 meter 累加），模型档位优先取
    显式注入值、否则取 ``tier_history`` 末项（回边/重复检索的档位归本轮）。
    复现指纹由装配层注入的十字段（含 preference_version）计算。

    Args:
        state: 当前 State（只读预算用量 / 档位历史 / run/session 标识）。
        deps: 生产依赖（用其 clock 取落账时刻，保持时钟可注入）。
        ledger: 成本账本端口（真实 PG 或显式注入的替身）。
        entry_meta: State 之外的元数据（user_id / 档位 / 指纹输入）。
    """
    tier = entry_meta.model_tier or (
        state.tier_history[-1] if state.tier_history else _NO_TIER
    )
    used = state.budget_used
    ledger.append(
        CostEntry(
            run_id=state.run_id,
            session_id=state.session_id,
            user_id=entry_meta.user_id,
            model_tier=tier,
            tokens=int(used.get("tokens", 0)),
            iterations=int(used.get("iterations", 0)),
            tool_calls=int(used.get("tool_calls", 0)),
            sandbox_minutes=float(used.get("sandbox_minutes", 0.0)),
            repro_fingerprint=compute_repro_fingerprint(entry_meta.repro_inputs),
            generation_path=entry_meta.generation_path,
            created_at=deps.clock.now(),
        )
    )
