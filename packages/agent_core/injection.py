"""M03 注入接口（§4.3，【推导·需评审】已固化到 injection.py）。

库内禁止 import 具体基础设施客户端；PG / Redis / 对象存储 / 向量库 /
model gateway / OTel 全部以 Protocol 注入，保证 §8.1 可用内存替身单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from packages.agent_core.types import AgentEvent, Message, ModelOutput, State


@runtime_checkable
class ModelGateway(Protocol):
    def complete(self, messages: list[Message], tier: str, stream: bool) -> ModelOutput: ...


@runtime_checkable
class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...       # 返回带 hash 的 ref
    def get(self, ref: str) -> bytes: ...                  # 读取时校验 hash


@runtime_checkable
class CheckpointStore(Protocol):
    def save(self, run_id: str, rev: int, node: str, state_ref: str, prev_hash: str) -> None: ...
    def load(self, run_id: str) -> State | None: ...
    def latest_rev(self, run_id: str) -> int: ...
    def hashes(self, run_id: str) -> list[str]: ...        # 供 hash 链重算断言


@runtime_checkable
class IdempotencyStore(Protocol):
    def get_or_reserve(self, idem_key: str, run_id: str) -> str | None: ...
    def count(self, run_id: str) -> int: ...


@runtime_checkable
class EventBus(Protocol):
    def emit(self, event: AgentEvent) -> None: ...
    def window(self, session_id: str, n: int) -> list[AgentEvent]: ...
    def all(self) -> list[AgentEvent]: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class Tracer(Protocol):
    def span(self, name: str, **attrs: Any) -> Any: ...


@runtime_checkable
class VectorIndex(Protocol):
    def search(self, query: str, top_k: int) -> list[Any]: ...


@runtime_checkable
class Lease(Protocol):
    """对象式租约（§4.1 说明：run() 用 lease.heartbeat() / lease.release() 无参调用）。"""

    run_id: str
    owner: str

    def heartbeat(self) -> bool: ...
    def release(self) -> None: ...


@runtime_checkable
class LeaseProvider(Protocol):
    """模块级租约入口（§4.1 L16–L19）；服务层注入真实实现。"""

    def acquire(self, run_id: str, owner: str, ttl_s: int) -> Lease: ...
    def heartbeat(self, run_id: str, owner: str) -> bool: ...
    def release(self, run_id: str, owner: str) -> None: ...
    def scan_expired(self, now: datetime) -> list[str]: ...


class NoOpTracer:
    """no-op span，观测不影响控制流（§8.1）。"""

    class _Span:
        def __enter__(self) -> NoOpTracer._Span:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_attribute(self, *args: Any, **kwargs: Any) -> None:
            return None

    def span(self, name: str, **attrs: Any) -> _Span:
        return self._Span()


@dataclass
class Deps:
    """run() 的全部注入依赖（§4.3）。生产由服务层构造，单测用内存替身。"""

    model: ModelGateway
    checkpoint_store: CheckpointStore
    idempotency_store: IdempotencyStore
    event_bus: EventBus
    clock: Clock
    tracer: Tracer = field(default_factory=NoOpTracer)
    lease_provider: LeaseProvider | None = None
    owner: str = "worker-1"
