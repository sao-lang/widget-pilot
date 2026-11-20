"""guard_hit 留证契约与行结构（§6.1 / §3.4 E）。

生产实现为 PgGuardHitStore（hash 链 + advisory lock + PG 失败保守拒绝）；
测试内存替身与 FPR 复算辅助函数位于 tests/support/guardrails.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from services.guardrails.types import GuardHitType, InputGuardHit


class GuardHitStore(Protocol):
    """guard_hit 留证存储协议（生产 PG 与测试替身共用）。"""

    @property
    def rows(self) -> list[GuardHitRow]:
        """按 id 升序的命中行。"""
        ...

    def log_hit(
        self,
        session_id: str,
        run_id: str,
        hit: InputGuardHit,
        trace_id: str,
        should_pass: bool | None = None,
    ) -> int:
        """追加命中留证，返回 id。"""
        ...

    def verify_chain(self) -> bool:
        """校验 hash 链完整。"""
        ...

    def false_positive_rate(self, labeled_should_pass: int) -> float:
        """复算误拦截率。"""
        ...


@dataclass(slots=True)
class GuardHitRow:
    """guard_hit 表一行（§6.1）。

    Attributes:
        id: 自增命中 id。
        session_id / run_id: 归属会话与执行。
        hit_type / rule_id / judgement / excerpt: 命中留证四字段。
        decision: 处置决策。
        trace_id: 全链路 trace。
        should_pass: 标注「该不该拦」（FPR 复算用，None 表示未标注）。
        prev_hash: 前一行的 row_hash，首行为 64 个 0；链防篡改。
        row_hash: 本行 hash = sha256(id|prev_hash|hit_type|rule_id|judgement|excerpt|decision|trace_id)。
    """

    id: int
    session_id: str
    run_id: str
    hit_type: GuardHitType
    rule_id: str
    judgement: str
    excerpt: str
    decision: str
    trace_id: str
    should_pass: bool | None
    prev_hash: str
    row_hash: str
