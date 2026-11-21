"""M03 注入接口（§4.3，【推导·需评审】已固化到 injection.py）。

库内禁止 import 具体基础设施客户端；PG / Redis / 对象存储 / 向量库 /
model gateway / OTel 全部以 Protocol 注入，保证 §8.1 可用内存替身单测。

类型改造后：所有 Protocol 方法签名零裸 ``Any``——追踪属性用 :data:`JsonValue`，
向量检索返回 :class:`Candidate`，Span 用独立 :class:`Span` Protocol 描述。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from packages.agent_core.types import (
    AgentEvent,
    Candidate,
    CheckpointNode,
    JsonValue,
    Message,
    ModelOutput,
    State,
)


@runtime_checkable
class Span(Protocol):
    """OTel span 的最小接口（上下文管理器 + 属性写入）。"""

    def __enter__(self) -> Self:
        """进入 span 作用域，返回自身。"""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出 span 作用域；异常信息由参数传入但不吞掉。"""
        ...

    def set_attribute(self, key: str, value: JsonValue) -> None:
        """写入一个 span 属性（值为 JSON 兼容类型）。"""
        ...


@runtime_checkable
class Tracer(Protocol):
    """链路追踪器：创建 span 注入点。"""

    def span(self, name: str, **attrs: JsonValue) -> Span:
        """创建名为 name 的 span，attrs 为初始属性（JSON 兼容值）。"""
        ...


@runtime_checkable
class ModelGateway(Protocol):
    """模型网关：直连 chat-completions 的注入点。"""

    def complete(self, messages: list[Message], tier: str, stream: bool) -> ModelOutput:
        """同步补全；tier 为模型档位，stream 表示是否流式。"""
        ...


@runtime_checkable
class CapabilityAudit(Protocol):
    """能力加载审计（ADR-003 未完成项3）：内核不 import 上层，鸭子协议解耦。"""

    def record(
        self,
        session_id: str,
        run_id: str,
        trace_id: str,
        capability_id: str,
        level: int,
        surface: str,
        decision: str,
        latency_ms: int,
    ) -> None:
        """记录一条 L1/L2 能力请求（只追加，不含原文/密钥）。"""
        ...


@runtime_checkable
class CapabilityGuard(Protocol):
    """能力加载闸门（ADR-003）：内核不 import 上层，用鸭子协议解耦。"""

    def check(
        self, capability_id: str, level: int, surface: str, count: int, tokens: int
    ) -> tuple[bool, str | None]:
        """返回 (是否放行, 拒绝原因)；token 为本次请求预计成本。"""
        ...


@runtime_checkable
class ObjectStore(Protocol):
    """对象存储：按 key 写、按带 hash 的 ref 读。"""

    def put(self, key: str, data: bytes) -> str:
        """写入字节并返回带 hash 的 ref。"""
        ...

    def get(self, ref: str) -> bytes:
        """按 ref 读取并校验 hash。"""
        ...


@runtime_checkable
class CheckpointStore(Protocol):
    """版本化 checkpoint 存储：按 (run_id, rev) 存状态与 hash 链。"""

    def save(self, run_id: str, rev: int, node: CheckpointNode, state_ref: str, prev_hash: str) -> None:
        """落一行 checkpoint（主键 (run_id, rev) 唯一）。"""
        ...

    def load(self, run_id: str) -> State | None:
        """读取最新 rev 的 State；无记录返回 None。"""
        ...

    def latest_rev(self, run_id: str) -> int:
        """返回该 run 当前最大 rev；无记录返回 -1。"""
        ...

    def hashes(self, run_id: str) -> list[str]:
        """返回各行 prev_hash（按 rev 排序），供 hash 链重算断言。"""
        ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """幂等副作用表：按 idem_key 预留/复用 result_ref。"""

    def get_or_reserve(self, idem_key: str, run_id: str) -> str | None:
        """命中返回既有 result_ref；否则占位并返回 None。"""
        ...

    def count(self, run_id: str) -> int:
        """返回该 run 已记录的幂等条目数。"""
        ...


@runtime_checkable
class EventBus(Protocol):
    """事件总线：写事件窗口与全量查询。"""

    def emit(self, event: AgentEvent) -> None:
        """发出一条事件。"""
        ...

    def window(self, session_id: str, n: int) -> list[AgentEvent]:
        """返回该会话最近 n 条事件。"""
        ...

    def all(self) -> list[AgentEvent]:
        """返回全部事件（供 seq 计算与回放）。"""
        ...


@runtime_checkable
class Clock(Protocol):
    """可注入时钟：decide 不读时钟，由调用方注入。"""

    def now(self) -> datetime:
        """返回当前时间（带时区）。"""
        ...


@runtime_checkable
class VectorIndex(Protocol):
    """向量检索索引：返回统一候选形态。"""

    def search(self, query: str, top_k: int) -> list[Candidate]:
        """按语义相似度返回 top_k 个候选。"""
        ...


@runtime_checkable
class Lease(Protocol):
    """对象式租约（§4.1 说明：run() 用 lease.heartbeat() / lease.release() 无参调用）。"""

    run_id: str
    """租约所属 run。"""
    owner: str
    """持锁者标识。"""

    def heartbeat(self) -> bool:
        """续约成功返回 True。"""
        ...

    def release(self) -> None:
        """释放租约。"""
        ...


@runtime_checkable
class LeaseProvider(Protocol):
    """模块级租约入口（§4.1 L16–L19）；服务层注入真实实现。"""

    def acquire(self, run_id: str, owner: str, ttl_s: int) -> Lease:
        """原子抢锁；同一 run 只有一个持锁者。"""
        ...

    def heartbeat(self, run_id: str, owner: str) -> bool:
        """按 (run_id, owner) 续约；非持锁者返回 False。"""
        ...

    def release(self, run_id: str, owner: str) -> None:
        """按 (run_id, owner) 释放租约。"""
        ...

    def scan_expired(self, now: datetime) -> list[str]:
        """扫描已过期且无终态的 run_id 列表。"""
        ...


class NoOpTracer:
    """no-op span，观测不影响控制流（§8.1）。"""

    class _Span:
        """空 span：进出作用域与写属性均为无操作。"""

        def __enter__(self) -> NoOpTracer._Span:
            """返回自身。"""
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            """无操作。"""
            return

        def set_attribute(self, key: str, value: JsonValue) -> None:
            """无操作。"""
            return

    def span(self, name: str, **attrs: JsonValue) -> _Span:
        """返回一个空 span（忽略 name / attrs）。"""
        return self._Span()


@dataclass
class Deps:
    """run() 的全部注入依赖（§4.3）。生产由服务层构造，单测用内存替身。"""

    model: ModelGateway
    """模型网关注入点。"""
    checkpoint_store: CheckpointStore
    """checkpoint 存储注入点。"""
    idempotency_store: IdempotencyStore
    """幂等表注入点。"""
    event_bus: EventBus
    """事件总线注入点。"""
    clock: Clock
    """时钟注入点。"""
    tracer: Tracer = field(default_factory=NoOpTracer)
    """链路追踪注入点；缺省 no-op。"""
    lease_provider: LeaseProvider | None = None
    """租约提供者；None 表示不启用租约。"""
    owner: str = "worker-1"
    """持锁者标识（worker 实例名）。"""
    capability_guard: CapabilityGuard | None = None
    """能力加载闸门（ADR-003）；生产必注入，None 时内核用确定性默认闸门。"""
    object_store: ObjectStore | None = None
    """对象存储（ADR-003 / B5）；生产必注入，用于能力结果落 ref。"""
    capability_audit: CapabilityAudit | None = None
    """能力加载审计（ADR-003 未完成项3）；None 时不记录（测试/无审计环境）。"""
