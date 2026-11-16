"""M03 版本化 checkpoint（§11.4 / §4.1 L14/L15）。

每个 super-step 同步落盘：rev 单调递增（主键 (run_id, rev)），prev_hash 成链
防篡改，大对象只存引用（对象存储）。hash 链可完整重算校验（I5）。

链定义：row[i].prev_hash = sha256(prev_row.prev_hash + hash(state_bytes[i]))，
首行 prev = ""。chain_valid 从对象存储回读 state_ref 内容重算每一环。
"""
from __future__ import annotations

import hashlib
import json

from packages.agent_core.injection import CheckpointStore
from packages.agent_core.types import State


def serialize_state(state: State) -> bytes:
    return json.dumps(state.snapshot(), sort_keys=True, default=str).encode("utf-8")


def save_checkpoint(store: CheckpointStore, state: State, node: str) -> str:
    """落盘一行 checkpoint；返回该行的链 hash（供下一段复用）。

    store 需具备 objects（FakeObjectStore / 生产对象存储）与 states 便捷属性；
    这两个是 §4.3 注入接口之外的测试便利，生产由 CheckpointStore 实现承载。
    """
    data = serialize_state(state)
    state_hash = hashlib.sha256(data).hexdigest()
    chain = store.hashes(state.run_id)            # 已有各行 prev_hash（按 rev 排序）
    prev = chain[-1] if chain else ""
    row_hash = hashlib.sha256((prev + state_hash).encode("utf-8")).hexdigest()

    objects = store.objects
    ref = objects.put(f"checkpoint/{state.run_id}/{state.rev}.json", data)
    store.save(state.run_id, state.rev, node, ref, row_hash)
    # 便捷缓存（内存替身需要；生产 CheckpointStore.load 从 state_ref 反序列化）
    if hasattr(store, "states"):
        store.states[state.run_id] = state
    return row_hash


def load_checkpoint(store: CheckpointStore, run_id: str) -> State | None:
    """取最新 rev 的 State。"""
    return store.load(run_id)


def chain_valid(store: CheckpointStore, run_id: str) -> bool:
    """重算 hash 链：每行 prev_hash 必须等于 sha256(前行链值 + 内容 hash)。"""
    rows = sorted(store.rows[run_id], key=lambda r: r["rev"]) if hasattr(store, "rows") else []
    if not rows:
        return False
    prev = ""
    objects = store.objects
    for row in rows:
        try:
            data = objects.get(row["state_ref"])
        except Exception:  # noqa: BLE001 - 对象引用失效/篡改 → hash 链校验失败（返回 False）
            return False
        state_hash = hashlib.sha256(data).hexdigest()
        expected = hashlib.sha256((prev + state_hash).encode("utf-8")).hexdigest()
        if row["prev_hash"] != expected:
            return False
        prev = row["prev_hash"]
    return True
