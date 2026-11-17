"""M03 §8.1 四件桩 + 测试基座。

完全脱离真实数据库 / 模型 / 前端：fake model / 内存 checkpoint store /
fake clock / fake object store，另有内存 EventBus 与 no-op Tracer。
所有桩可脚本化、可断言调用次数，支撑 T1–T27。
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack, cast

import pytest

from packages.agent_core.errors import CoreError
from packages.agent_core.injection import NoOpTracer
from packages.agent_core.state.checkpoint import CheckpointRow
from packages.agent_core.types import (
    AgentEvent,
    Message,
    ModelOutput,
    State,
    Usage,
)
from packages.contracts.enums import ModelTier


class TimedeltaArgs(TypedDict, total=False):
    """FakeClock.advance 的时间增量参数（确定键，取自 datetime.timedelta）。"""

    days: float
    seconds: float
    microseconds: int
    milliseconds: float
    minutes: float
    hours: float
    weeks: float


class ModelCall(TypedDict):
    """FakeModel 记录的一次调用（确定键）。"""

    messages: list[Message]
    tier: str
    stream: bool


class RaiseSpec(TypedDict):
    """脚本项 ``{"raise": {...}}`` 的异常描述（确定键）。"""

    code: str
    message: str
    retryable: bool | None


type ScriptItem = ModelOutput | Exception | str | dict[str, object]
"""FakeModel 脚本项：预置输出 / 异常 / 文本 / 原始 dict（含 {"raise": ...}）。"""


class FakeClock:
    """可冻结、可 +N 天的时钟（§8.1：验证租约过期与跨天恢复）。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: Unpack[TimedeltaArgs]) -> None:
        self._now += timedelta(**kwargs)

    def freeze(self, at: datetime) -> None:
        self._now = at


class FakeObjectStore:
    """内存 dict[key, bytes]，put 返回带 hash 的 ref，get 校验 hash。"""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        ref = f"{key}#sha256:{digest}"
        self.data[ref] = data
        return ref

    def get(self, ref: str) -> bytes:
        key, _, tagged = ref.partition("#sha256:")
        if not tagged:
            # 允许直接按 key 取（桩内宽松），但带 hash 时必校验
            hits = [v for k, v in self.data.items() if k.partition("#")[0] == key]
            if not hits:
                raise KeyError(ref)
            return hits[0]
        data = self.data.get(ref)
        if data is None:
            raise KeyError(ref)
        if hashlib.sha256(data).hexdigest() != tagged:
            from packages.agent_core.errors import CoreError
            raise CoreError.business("CONTRACT_MISMATCH", "object hash mismatch", ref)
        return data


class InMemoryCheckpointStore:
    """dict[run_id, list[(rev, node, state_ref, prev_hash)]] + state blob。"""

    def __init__(self, objects: FakeObjectStore | None = None) -> None:
        self.rows: dict[str, list[CheckpointRow]] = {}
        self.states: dict[str, State] = {}
        self.objects = objects or FakeObjectStore()

    def _serialize(self, state: State) -> bytes:
        import json
        return json.dumps(state.snapshot(), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")

    def save(self, run_id: str, rev: int, node: str, state_ref: str, prev_hash: str) -> None:
        rows = self.rows.setdefault(run_id, [])
        # 主键 (run_id, rev)：重复 rev 直接抛，供 I4 断言
        if any(r["rev"] == rev for r in rows):
            raise ValueError(f"duplicate checkpoint (run_id={run_id}, rev={rev})")
        rows.append({"rev": rev, "node": node, "state_ref": state_ref, "prev_hash": prev_hash})

    def save_state(self, run_id: str, state: State) -> str:
        """便捷方法：序列化 state 到对象存储并写 checkpoint 行。"""
        ref = self.objects.put(f"checkpoint/{run_id}/{state.rev}.json", self._serialize(state))
        prev = self.hashes(run_id)[-1] if self.hashes(run_id) else ""
        self.save(run_id, state.rev, str(state.phase), ref, prev)
        self.states[run_id] = state
        return ref

    def load(self, run_id: str) -> State | None:
        return self.states.get(run_id)

    def latest_rev(self, run_id: str) -> int:
        rows = self.rows.get(run_id, [])
        return max((r["rev"] for r in rows), default=-1)

    def hashes(self, run_id: str) -> list[str]:
        return [r["prev_hash"] for r in sorted(self.rows.get(run_id, []), key=lambda r: r["rev"])]


class InMemoryIdempotencyStore:
    """dict[idem_key, result_ref]；命中复用，不重复执行。"""

    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self.reserved: set[str] = set()

    def get_or_reserve(self, idem_key: str, run_id: str) -> str | None:
        if idem_key in self.entries:
            return self.entries[idem_key]
        self.reserved.add(idem_key)
        return None

    def commit(self, idem_key: str, result_ref: str) -> None:
        self.entries[idem_key] = result_ref

    def count(self, run_id: str) -> int:
        return len(self.entries)


class InMemoryEventBus:
    """内存数组，记录全部 emit 的 event。"""

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def window(self, session_id: str, n: int) -> list[AgentEvent]:
        matches = [e for e in self.events if e.session_id == session_id]
        return matches[-n:]

    def all(self) -> list[AgentEvent]:
        return list(self.events)


class FakeModel:
    """脚本化 fake model.complete：按脚本返回固定 ModelOutput。

    可注入 429/5xx（抛 CoreError.infra）、半截 JSON、越权代码；
    记录调用次数，供 T1/T3/T22「模型只在 CALL_MODEL 出现」断言。
    """

    def __init__(self, script: list[ScriptItem] | None = None) -> None:
        self.script: list[ScriptItem] = list(script or [])
        self.calls: list[ModelCall] = []

    def set_script(self, script: list[ScriptItem]) -> FakeModel:
        """预置脚本项队列（FIFO），返回自身便于链式调用。"""
        self.script = list(script)
        return self

    def complete(self, messages: list[Message], tier: str = "standard", stream: bool = True) -> ModelOutput:
        """按脚本弹出一项；记录本次调用参数供断言。"""
        self.calls.append({"messages": list(messages), "tier": tier, "stream": stream})
        model_tier = cast(ModelTier, tier)
        if not self.script:
            return ModelOutput(content="{}", usage=Usage(), tier=model_tier)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ModelOutput):
            return item
        if isinstance(item, str):
            return ModelOutput(content=item, usage=Usage(input_tokens=10, output_tokens=5), tier=model_tier)
        if "raise" in item:
            # {"raise": {"code", "message", "retryable"}} → 抛指定 CoreError
            raw_meta = item["raise"]
            meta = cast(RaiseSpec, raw_meta)
            raise CoreError(
                meta.get("code", "MODEL_ERROR"),
                meta.get("message", ""),
                retryable=meta.get("retryable"),
            )
        # dict → 作为 raw 内容
        import json
        return ModelOutput(content=json.dumps(item), usage=Usage(), tier=model_tier)

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def object_store() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def checkpoint_store(object_store: FakeObjectStore) -> InMemoryCheckpointStore:
    return InMemoryCheckpointStore(object_store)


@pytest.fixture
def idem_store() -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def tracer() -> NoOpTracer:
    return NoOpTracer()


@pytest.fixture
def fake_model() -> FakeModel:
    return FakeModel()


class FakeLease:
    def __init__(self, run_id: str, owner: str) -> None:
        self.run_id = run_id
        self.owner = owner
        self.heartbeats = 0
        self.released = False

    def heartbeat(self) -> bool:
        self.heartbeats += 1
        return True

    def release(self) -> None:
        self.released = True


class FakeLeaseProvider:
    """内存租约：原子抢锁（同 run_id 只有一个 owner），支持心跳/释放/过期扫描。"""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.holders: dict[str, str] = {}
        self.until: dict[str, datetime] = {}
        self.leases: list[FakeLease] = []
        self.terminal: set[str] = set()

    def acquire(self, run_id: str, owner: str, ttl_s: int) -> FakeLease:
        now = self.clock.now()
        current = self.holders.get(run_id)
        if current is not None and current != owner and self.until.get(run_id, now) > now:
            from packages.agent_core.errors import CoreError
            raise CoreError.business("HUMAN_REQUIRED", "lease held by another owner")
        self.holders[run_id] = owner
        self.until[run_id] = now + timedelta(seconds=ttl_s)
        lease = FakeLease(run_id, owner)
        self.leases.append(lease)
        return lease

    def heartbeat(self, run_id: str, owner: str) -> bool:
        if self.holders.get(run_id) != owner:
            return False
        self.until[run_id] = self.clock.now() + timedelta(seconds=30)
        return True

    def release(self, run_id: str, owner: str) -> None:
        if self.holders.get(run_id) == owner:
            del self.holders[run_id]

    def scan_expired(self, now: datetime) -> list[str]:
        return [
            rid for rid, until in self.until.items()
            if until < now and rid not in self.terminal
        ]


@pytest.fixture
def lease_provider(clock: FakeClock) -> FakeLeaseProvider:
    return FakeLeaseProvider(clock)


@pytest.fixture
def sample_state() -> State:
    return State.initial("session-1", "run-1", "做一个 4x4 月视图日历", "4x4")
