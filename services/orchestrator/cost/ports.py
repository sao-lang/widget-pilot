"""
purity-exempt-file: 本文件为 @runtime_checkable Protocol 成本账本接口，append 方法体 `...` 是契约声明非占位，参数 entry 已在 Args 段说明。
成本账端口与数据（M07 §3.4 D / B7）。

``CostEntry`` 是一条成本账目（每 run 一行，run_id 唯一）；``CostLedgerPort``
只定义鸭子协议，生产实现为 PG（``pg_ledger.py``），测试显式注入替身。
模块不 import 任何具体存储，避免循环依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from services.orchestrator.memory.repro import ReproInputs


@dataclass(frozen=True, slots=True)
class CostEntry:
    """一条成本账目（B7：每 run 一行，按档位分账）。

    Attributes:
        run_id: 运行标识（账本唯一键，幂等依据）。
        session_id: 会话标识（归属索引）。
        user_id: 租户标识（分账归属）。
        model_tier: 本轮模型档位（cheap/standard/frontier）。
        tokens: 累计 token 数（input + output）。
        iterations: 累计 super-step 轮次。
        tool_calls: 累计工具调用次数。
        sandbox_minutes: 累计沙箱分钟数。
        repro_fingerprint: 该 run 的复现指纹（含 preference_version）。
        generation_path: 生成路径标签（ADR-019 D1；deterministic/cloud-fallback/
            llm-only/llm-rework），供三口径统计 GROUP BY。
        created_at: 落账时刻（UTC，带时区）。
    """

    run_id: str
    session_id: str
    user_id: str
    model_tier: str
    tokens: int
    iterations: int
    tool_calls: int
    sandbox_minutes: float
    repro_fingerprint: str
    generation_path: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CostEntryMeta:
    """构造 ``CostEntry`` 所需的、``State`` 之外的元数据。

    ``State`` 承载四维用量与 run/session 标识，但不承载 user_id 与复现指纹
    输入；本对象由装配层（worker）从会话与配置注入。

    Attributes:
        user_id: 租户标识（由会话注入，State 无此字段）。
        model_tier: 本轮模型档位；None 时取 ``state.tier_history`` 末项。
        repro_inputs: 复现指纹十字段（``preference_version`` 由装配层注入）。
        generation_path: 生成路径标签（ADR-019 D2）；默认 llm-only 兼容未注入
            的旧调用点，生产由 dispatcher 按分支注入真实值。
    """

    user_id: str
    model_tier: str | None = None
    repro_inputs: ReproInputs = field(default_factory=ReproInputs)
    generation_path: str = "llm-only"


@runtime_checkable
class CostLedgerPort(Protocol):
    """成本账本端口（真实实现 = PG，测试显式注入替身）。"""

    def append(self, entry: CostEntry) -> None:
        """追加一条成本账目；同 run_id 幂等（不重复记账）。"""
        ...
