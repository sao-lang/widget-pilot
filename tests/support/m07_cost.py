"""测试替身（test double）：M07 成本账本的内存实现，生产源码禁止 import。

与 PG 实现共用端口协议（``CostLedgerPort``），只把持久化换成 list，
供装配/适配器测试显式注入；幂等语义与 PG 一致（同 run_id 不重复记账）。
"""
from __future__ import annotations

from services.orchestrator.cost.ports import CostEntry

__all__ = ["InMemoryCostLedger"]


class InMemoryCostLedger:
    """成本账本内存替身：按 run_id 幂等追加（仅测试注入用）。

    Attributes:
        entries: 已追加的账目列表（run_id 唯一，按追加顺序，供断言）。
    """

    def __init__(self) -> None:
        """初始化空账目列表。"""
        self.entries: list[CostEntry] = []

    def append(self, entry: CostEntry) -> None:
        """追加一条账目；同 run_id 已存在则跳过（与 PG 幂等语义一致）。"""
        if any(existing.run_id == entry.run_id for existing in self.entries):
            return
        self.entries.append(entry)
