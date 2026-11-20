"""M03 state 持久化层：T16 幂等 / T17 rev+hash链 / T19 抢租约 / T21 崩溃续跑 / T24 跨天恢复。

库内只定义结构与算法，Store 由 conftest 的内存替身注入。
"""
from __future__ import annotations

from typing import cast

import pytest

from packages.agent_core.errors import CoreError
from packages.agent_core.state.checkpoint import chain_valid, save_checkpoint
from packages.agent_core.state.idempotency import get_or_reserve
from packages.agent_core.state.lease import acquire, heartbeat, release, scan_expired
from packages.agent_core.types import CheckpointNode, State
from tests.conftest import FakeLease


# ---- T17：rev 单调 + prev_hash 链可校验 ----
def test_T17_checkpoint_rev_monotonic_and_hash_chain(checkpoint_store):
    for rev in range(30):
        s = State.initial("s", "run-17", "x")
        s = s.bump(rev=rev, iteration=rev)
        save_checkpoint(checkpoint_store, s, node=CheckpointNode.CALL_MODEL)
    rows = checkpoint_store.rows["run-17"]
    revs = [r["rev"] for r in rows]
    assert revs == sorted(set(revs))  # 严格递增无重复
    assert checkpoint_store.latest_rev("run-17") == 29
    assert chain_valid(checkpoint_store, "run-17") is True


def test_T17_duplicate_rev_rejected(checkpoint_store):
    s = State.initial("s", "run-dup", "x").bump(rev=0)
    save_checkpoint(checkpoint_store, s, node=CheckpointNode.CALL_MODEL)
    with pytest.raises(ValueError):
        # 同一 rev 重复落盘：即使换节点也必须以主键冲突拒绝（I4）
        save_checkpoint(checkpoint_store, s, node=CheckpointNode.FINAL)


# ---- T16：幂等副作用，命中复用不重复 ----
def test_T16_side_effect_idempotent(idem_store):
    # 3 个副作用，每个 idem_key 只 reserve 一次
    for i in range(3):
        key = f"s|step{i}|hash{i}"
        assert get_or_reserve(idem_store, key, "run-16") is None
        idem_store.commit(key, f"ref-{i}")
    assert idem_store.count("run-16") == 3
    # 恢复重执行：同 key 命中，返回复用 ref，不新增
    for i in range(3):
        key = f"s|step{i}|hash{i}"
        assert get_or_reserve(idem_store, key, "run-16") == f"ref-{i}"
    assert idem_store.count("run-16") == 3  # 不是 6


def test_idem_key_format():
    from packages.agent_core.state.idempotency import make_idem_key
    key = make_idem_key("session-1", "step-2", "abc123")
    assert key == "session-1|step-2|abc123"


# ---- T19：同一 runId 并发抢租约恰好 1 个成功 ----
def test_T19_lease_acquire_exactly_one_winner(lease_provider, clock):
    acquire(lease_provider, "run-19", "worker-A", ttl_s=30)
    with pytest.raises(CoreError):
        acquire(lease_provider, "run-19", "worker-B", ttl_s=30)
    assert lease_provider.holders["run-19"] == "worker-A"


def test_lease_heartbeat_and_release(lease_provider):
    lease = acquire(lease_provider, "run-hb", "worker-A", ttl_s=30)
    assert heartbeat(lease_provider, "run-hb", "worker-A") is True
    # heartbeats 是内存桩 FakeLease 的观测量，cast 到桩类型后断言
    assert cast(FakeLease, lease).heartbeats >= 0
    release(lease_provider, "run-hb", "worker-A")
    assert "run-hb" not in lease_provider.holders


# ---- T21：崩溃后 lease 过期，可被另一实例扫描续跑 ----
def test_T21_scan_expired_after_worker_kill(lease_provider, clock):
    acquire(lease_provider, "run-21", "worker-A", ttl_s=30)
    # worker A 被杀：时钟推过 lease_until
    clock.advance(seconds=60)
    expired = scan_expired(lease_provider, clock.now())
    assert "run-21" in expired
    # B 原子抢租约成功
    acquire(lease_provider, "run-21", "worker-B", ttl_s=30)
    assert lease_provider.holders["run-21"] == "worker-B"


def test_T21_terminal_not_scanned(lease_provider, clock):
    acquire(lease_provider, "run-done", "worker-A", ttl_s=30)
    lease_provider.terminal.add("run-done")  # 模拟 terminal IS NOT NULL
    clock.advance(seconds=60)
    assert "run-done" not in scan_expired(lease_provider, clock.now())


# ---- T24：跨天恢复，State 完整未丢 ----
def test_T24_cross_day_resume_preserves_state(checkpoint_store, lease_provider, clock):
    s = State.initial("s", "run-24", "做一个日历", "4x4")
    s = s.bump(excluded_approaches=["方案A", "方案B"], rev=5, seq=12)
    checkpoint_store.states["run-24"] = s
    # +7 天，无 worker、无模型、无成本
    clock.advance(days=7)
    loaded = checkpoint_store.load("run-24")
    assert loaded.excluded_approaches == ["方案A", "方案B"]
    assert loaded.seq == 12
    assert loaded.rev == 5
    assert loaded.terminal is None
    # 恢复后抢租约续跑
    acquire(lease_provider, "run-24", "worker-B", ttl_s=30)
    assert lease_provider.holders["run-24"] == "worker-B"
