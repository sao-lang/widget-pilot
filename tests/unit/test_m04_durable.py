"""M04 durable execution 适配器单测（§8.2 A3/A4/A5/A7/A10/A11）。

验证 M04 落库适配器实现的 M03 Protocol 满足 §3.4 不变量：
I1 checkpoint rev 单调、I2 hash 链可校验、I3 单持锁者、I4 幂等条数、
I5 seq 连续。复用 tests/conftest.py 的内存桩，不依赖 Docker。
"""

from __future__ import annotations

import pytest

from packages.agent_core.errors import CoreError
from packages.agent_core.state.checkpoint import chain_valid, save_checkpoint
from packages.agent_core.state.idempotency import get_or_reserve, make_idem_key
from packages.agent_core.state.lease import (
    acquire as lease_acquire,
)
from packages.agent_core.state.lease import (
    heartbeat as lease_heartbeat,
)
from packages.agent_core.state.lease import (
    release as lease_release,
)
from packages.agent_core.state.lease import (
    scan_expired,
)
from packages.agent_core.types import CheckpointNode, State
from services.control_plane.sessions.sse import SSEWindow
from tests.conftest import (
    FakeClock,
    FakeLeaseProvider,
    InMemoryCheckpointStore,
    InMemoryIdempotencyStore,
)


class TestCheckpointChain:
    """I1/I2/A10：rev 单调 + hash 链可完整校验。"""

    def test_rev_monotonic_and_chain_valid(
        self,
        checkpoint_store: InMemoryCheckpointStore,
        sample_state: State,
    ) -> None:
        """30 个 super-step 落盘：rev 严格递增、hash 链可重算通过（A10）。"""
        state = sample_state
        for rev in range(1, 31):
            object.__setattr__(state, "rev", rev)
            save_checkpoint(checkpoint_store, state, CheckpointNode.FINAL)
        assert checkpoint_store.latest_rev(state.run_id) == 30
        assert chain_valid(checkpoint_store, state.run_id)

    def test_duplicate_rev_rejected(
        self, checkpoint_store: InMemoryCheckpointStore
    ) -> None:
        """重复 (run_id, rev) 抛错（I1：主键约束）。"""
        # 直接用 store.save 断言主键唯一
        checkpoint_store.save("run-x", 1, CheckpointNode.FINAL, "ref1", "h1")
        with pytest.raises(ValueError):
            checkpoint_store.save("run-x", 1, CheckpointNode.FINAL, "ref2", "h2")

    def test_state_snapshot_roundtrip_cross_day_recovery(
        self, sample_state: State
    ) -> None:
        """snapshot → from_snapshot 无损还原（A7 跨天恢复的序列化边界）。"""
        import json

        data = json.loads(json.dumps(sample_state.snapshot(), default=str))
        restored = State.from_snapshot(data)
        assert restored.session_id == sample_state.session_id
        assert restored.run_id == sample_state.run_id
        assert restored.rev == sample_state.rev
        assert restored.excluded_approaches == sample_state.excluded_approaches
        assert restored.spec.model_dump() == sample_state.spec.model_dump()


class TestLease:
    """I3/A4：同一 runId 同时只有一个持锁者；过期扫描。"""

    def test_single_holder(self, clock: FakeClock) -> None:
        """两个 worker 抢同一 run：恰好 1 个成功（A4）。"""
        provider = FakeLeaseProvider(clock)
        lease_acquire(provider, "run-1", "worker-a", ttl_s=30)
        # 同 run 第二持锁者抛 CoreError（业务冲突码 HUMAN_REQUIRED），非盲断言 Exception
        with pytest.raises(CoreError):
            lease_acquire(provider, "run-1", "worker-b", ttl_s=30)

    def test_expired_lease_scan(self, clock: FakeClock) -> None:
        """租约过期后扫描命中，可恢复（§11.4 step 3）。"""
        provider = FakeLeaseProvider(clock)
        lease_acquire(provider, "run-1", "worker-a", ttl_s=30)
        assert scan_expired(provider, clock.now()) == []
        clock.advance(seconds=31)
        assert "run-1" in scan_expired(provider, clock.now())

    def test_heartbeat_and_release(self, clock: FakeClock) -> None:
        """心跳续租与主动释放（§11.4 step 2/5）。"""
        provider = FakeLeaseProvider(clock)
        lease_acquire(provider, "run-1", "worker-a", ttl_s=30)
        assert lease_heartbeat(provider, "run-1", "worker-a") is True
        lease_release(provider, "run-1", "worker-a")
        # 释放后别人可抢占
        lease_acquire(provider, "run-1", "worker-b", ttl_s=30)


class TestIdempotency:
    """I4/A5：同一 idem_key 只 1 条，恢复重执行不重复副作用。"""

    def test_reserve_once(self) -> None:
        """同名 idem_key 二次 get_or_reserve 命中复用（A5：新增 0 条）。"""
        store = InMemoryIdempotencyStore()
        key = make_idem_key("session-1", "emit_artifact", "abc123")
        assert get_or_reserve(store, key, "run-1") is None
        store.commit(key, "ref-1")
        assert get_or_reserve(store, key, "run-1") == "ref-1"
        assert store.count("run-1") == 1


class TestSSEWindow:
    """I5/A11：seq 连续、Last-Event-ID 按序续传。"""

    def test_resume_from_last_event_id(self) -> None:
        """从 Last-Event-ID=2 续传，得到 seq 3,4（A11：无重复无倒序）。"""
        window = SSEWindow(window_n=500)
        for seq in range(1, 6):
            window.emit(seq, "run", {"n": seq})
        resumed = window.since(last_event_id=2)
        assert [e["seq"] for e in resumed] == [3, 4, 5]

    def test_window_trims_old_events(self) -> None:
        """超过窗口大小的旧事件被裁剪（§4.5 保留最近 N 条）。"""
        window = SSEWindow(window_n=3)
        for seq in range(1, 6):
            window.emit(seq, "run", {})
        assert [e["seq"] for e in window.all()] == [3, 4, 5]
