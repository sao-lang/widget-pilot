"""测试替身（test doubles）：M06 护栏内存实现，生产代码禁止 import。

实现从 services/guardrails 原样迁移，仅保留中文 docstring 与行为；
数据类/枚举（AuditEvent、GuardHitRow、GuardHitType、InputGuardHit、
IntentRouteLogRow 等）从生产模块导入，避免重复定义导致类型漂移。
"""

from __future__ import annotations

import hashlib
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from packages.contracts.types import TaskFields
from services.guardrails.audit import AuditEvent
from services.guardrails.change_gate import ConfigSnapshot, diff_config
from services.guardrails.guard_hit import GuardHitRow
from services.guardrails.telemetry import IntentRouteLogRow, NeedSkillLogRow
from services.guardrails.types import (
    GuardHitType,
    HitDecision,
    InputGuardHit,
    RouteLayer,
    ValidationResult,
)

__all__ = [
    "AuditEvent",
    "GuardHitRow",
    "GuardHitType",
    "GuardrailStore",
    "HitDecision",
    "InMemoryAuditStore",
    "InMemoryGuardHitStore",
    "InputGuardHit",
    "IntentRouteLogRow",
    "IntentRouteLogStore",
    "NeedSkillLogRow",
    "NeedSkillLogStore",
    "RegressionGate",
    "RouteLayer",
    "false_positive_rate",
    "log_hit",
    "pass_through_validator",
    "verify_chain",
]

# validate_chain 测试入口可注入的五类内存留证存储（键集合确定，取代 dict[str, Any]）。
type GuardrailStore = (
    InMemoryAuditStore
    | InMemoryGuardHitStore
    | IntentRouteLogStore
    | NeedSkillLogStore
)


@dataclass(slots=True)
class InMemoryAuditStore:
    """内存只追加审计存储（对象存储版由 M05 注入，同构可插拔）。

    Attributes:
        events: 审计事件列表，按追加顺序。
        _lock: 保护 append 的原子性。
    """

    events: list[AuditEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append(self, event: AuditEvent) -> None:
        """追加一条审计事件（只追加，无更新/删除 API）。"""
        with self._lock:
            self.events.append(event)

    def all_events(self) -> tuple[AuditEvent, ...]:
        """按追加顺序返回全部事件（供审计复算）。"""
        return tuple(self.events)


@dataclass(slots=True)
class InMemoryGuardHitStore:
    """内存只追加存储（构造后只 append，不 UPDATE/DELETE）。

    Attributes:
        rows: 命中行列表，按 id 升序追加。
        _lock: 保护 id 分配 + prev_hash 读取 + append 的原子性（§4.5 只追加原子计数）。
    """

    rows: list[GuardHitRow] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log_hit(
        self,
        session_id: str,
        run_id: str,
        hit: InputGuardHit,
        trace_id: str,
        should_pass: bool | None = None,
    ) -> int:
        """方法化留证入口（与 PgGuardHitStore 同签名，供 validate_chain 统一调用）。"""
        return log_hit(session_id, run_id, hit, trace_id, self, should_pass)

    def verify_chain(self) -> bool:
        """方法化 hash 链校验（与 PgGuardHitStore 同签名）。"""
        return verify_chain(self)

    def false_positive_rate(self, labeled_should_pass: int) -> float:
        """方法化 FPR（与 PgGuardHitStore 同签名）。"""
        return false_positive_rate(self, labeled_should_pass)


def _chain_hash(row: GuardHitRow) -> str:
    """计算本行 hash，覆盖全部留证字段（防 decision/hit_type 篡改）。"""
    raw = "|".join(
        [
            str(row.id),
            row.session_id,
            row.run_id,
            row.prev_hash,
            row.hit_type.value,
            row.rule_id,
            row.judgement,
            row.excerpt,
            row.decision,
            row.trace_id,
            "1" if row.should_pass else "0" if row.should_pass is False else "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def log_hit(
    session_id: str,
    run_id: str,
    hit: InputGuardHit,
    trace_id: str,
    store: InMemoryGuardHitStore,
    should_pass: bool | None = None,
) -> int:
    """追加一条命中留证，返回自增 id（§4.3 log_hit）。

    整个「取 id → 读 prev_hash → 算 hash → append」在锁内完成，避免并发
    命中拿到重复 id / prev_hash 导致 hash 链断裂（§4.5 只追加原子计数）。
    """
    with store._lock:
        row_id = len(store.rows) + 1
        # 前一行 hash 作为本行 prev_hash；首行用全 0 作为创世链接
        prev_hash = store.rows[-1].row_hash if store.rows else "0" * 64
        decision = (
            hit.decision.value if hasattr(hit.decision, "value") else str(hit.decision)
        )
        row = GuardHitRow(
            id=row_id,
            session_id=session_id,
            run_id=run_id,
            hit_type=hit.hit_type,
            rule_id=hit.rule_id,
            judgement=hit.judgement,
            excerpt=hit.excerpt,
            decision=decision,
            trace_id=trace_id,
            should_pass=should_pass,
            prev_hash=prev_hash,
            row_hash="",  # 先占位，算出 chain hash 后回填
        )
        row.row_hash = _chain_hash(row)
        store.rows.append(row)
        return row_id


def verify_chain(store: InMemoryGuardHitStore) -> bool:
    """校验 hash 链完整：每行 prev_hash 等于前行 row_hash 且本行 row_hash 可复算。"""
    for index, row in enumerate(store.rows):
        expected_prev = store.rows[index - 1].row_hash if index > 0 else "0" * 64
        if row.prev_hash != expected_prev:
            return False
        if row.row_hash != _chain_hash(row):
            return False
    return True


def false_positive_rate(
    store: InMemoryGuardHitStore,
    labeled_should_pass: int,
) -> float:
    """误拦截率 FPR = 被拦且标注应放行 / 标注应放行总数（§3.4 E）。"""
    if labeled_should_pass <= 0:
        return 0.0
    false_positives = sum(1 for row in store.rows if row.should_pass is True)
    return false_positives / labeled_should_pass


@dataclass(slots=True)
class IntentRouteLogStore:
    """IntentRouteLog 只追加存储。

    Attributes:
        rows: 裁定记录，按追加顺序排列。
    """

    rows: list[IntentRouteLogRow] = field(default_factory=list)

    def append(self, row: IntentRouteLogRow) -> None:
        """追加一条裁定记录。"""
        self.rows.append(row)

    def stats(self) -> dict[str, float]:
        """从日志复算各级占比与升级率（L3+L4 占比）。"""
        total = len(self.rows)
        if total == 0:
            return {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0, "escalation_rate": 0.0}
        counts = Counter(row.route_layer for row in self.rows)
        result = {layer.value: counts[layer] / total for layer in RouteLayer}
        result["escalation_rate"] = (
            counts[RouteLayer.L3] + counts[RouteLayer.L4]
        ) / total
        return result


@dataclass(slots=True)
class NeedSkillLogStore:
    """NeedSkillLog 只追加存储。

    Attributes:
        rows: 决策记录，按追加顺序排列。
    """

    rows: list[NeedSkillLogRow] = field(default_factory=list)

    def append(self, row: NeedSkillLogRow) -> None:
        """追加一条决策记录。"""
        self.rows.append(row)

    def decisions(self) -> Counter:
        """按 decision 计数（A13：四种越界输入均 REJECT）。"""
        return Counter(row.decision for row in self.rows)


@dataclass(slots=True)
class RegressionGate:
    """测试用回归门禁钩子（记录触发次数与最近一次变更集，供 A16 断言）。

    Attributes:
        trigger: 测试注入的回调，接受变更项列表。
        calls: 已触发次数。
        last_change: 最近一次触发的变更项列表。
    """

    trigger: Callable[[list[str]], object] | None = None
    calls: int = 0
    last_change: list[str] = field(default_factory=list)

    def check(self, baseline: ConfigSnapshot, current: ConfigSnapshot) -> bool:
        """对比快照；有变更则触发门禁并返回 True，无变更返回 False。"""
        changed = diff_config(baseline, current)
        if not changed:
            return False
        self.calls += 1
        self.last_change = changed
        if self.trigger is not None:
            self.trigger(changed)
        return True


def pass_through_validator(description: str, fields: TaskFields) -> ValidationResult:
    """M06 未接线场景的放行桩：保持旧 Dispatcher 测试的无护栏行为。"""
    return ValidationResult(
        ok=True,
        trace_id=f"{fields.get('session_id', '')}:{fields.get('run_id', '')}",
    )
