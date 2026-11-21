"""M03 版本化 checkpoint（§11.4 / §4.1 L14/L15）。

每个 super-step 同步落盘：rev 单调递增（主键 (run_id, rev)），prev_hash 成链
防篡改，大对象只存引用（对象存储）。hash 链可完整重算校验（I5）。

链定义：row[i].prev_hash = sha256(prev_row.prev_hash + hash(state_bytes[i]))，
首行 prev = ""。chain_valid 从对象存储回读 state_ref 内容重算每一环。

类型改造后：便利属性（objects / states / rows）用 :class:`CheckpointStoreExtras`
Protocol 与 :class:`CheckpointRow` TypedDict 显式声明，取代无类型的动态属性访问。
"""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, TypedDict, cast

from packages.agent_core.injection import CheckpointStore, ObjectStore
from packages.agent_core.types import CheckpointNode, State


class CheckpointRow(TypedDict):
    """checkpoint 表的一行（确定键）。"""

    rev: int
    """版本号（主键 part）。"""
    node: CheckpointNode
    """落盘节点（触发落盘的判定分支或 FINAL 收尾；规则 5 用枚举）。"""
    state_ref: str
    """状态大对象的存储引用。"""
    prev_hash: str
    """链上前一行 hash（首行为空串）。"""


# 注：extra 属性（objects/states/rows）在测试替身上存在，生产实现可能不同；
# 用 Protocol 组合声明后经 cast 访问，hasattr 守卫保持原行为不变。
class CheckpointStoreExtras(CheckpointStore, Protocol):
    """带测试替身便利属性的 checkpoint 存储（§4.3 注入接口之外的测试便利）。"""

    objects: ObjectStore
    """对象存储（放状态大对象）。"""
    states: dict[str, State]
    """run_id → 最新 State 的便捷缓存。"""
    rows: dict[str, list[CheckpointRow]]
    """run_id → 所有 checkpoint 行的便捷缓存。"""


def serialize_state(state: State) -> bytes:
    """把 State 快照序列化为确定性字节（sort_keys + default=str）。"""
    return json.dumps(state.snapshot(), sort_keys=True, default=str).encode("utf-8")


def save_checkpoint(store: CheckpointStore, state: State, node: CheckpointNode) -> str:
    """落盘一行 checkpoint；返回该行的链 hash（供下一段复用）。

    store 需具备 objects（FakeObjectStore / 生产对象存储）与 states 便捷属性；
    这两个是 §4.3 注入接口之外的测试便利，生产由 CheckpointStore 实现承载。
    """
    data = serialize_state(state)
    state_hash = hashlib.sha256(data).hexdigest()
    chain = store.hashes(state.run_id)            # 已有各行 prev_hash（按 rev 排序）
    prev = chain[-1] if chain else ""
    row_hash = hashlib.sha256((prev + state_hash).encode("utf-8")).hexdigest()

    extras = cast(CheckpointStoreExtras, store)
    ref = extras.objects.put(f"checkpoint/{state.run_id}/{state.rev}.json", data)
    store.save(state.run_id, state.rev, node, ref, row_hash)
    # 便捷缓存（测试替身需要；生产 CheckpointStore.load 从 state_ref 反序列化）
    if hasattr(store, "states"):
        extras.states[state.run_id] = state
    return row_hash


def load_checkpoint(store: CheckpointStore, run_id: str) -> State | None:
    """取最新 rev 的 State。"""
    return store.load(run_id)


def chain_valid(store: CheckpointStore, run_id: str) -> bool:
    """重算 hash 链：每行 prev_hash 必须等于 sha256(前行链值 + 内容 hash)。"""
    extras = cast(CheckpointStoreExtras, store)
    raw_rows = extras.rows.get(run_id, []) if hasattr(store, "rows") else []
    rows = sorted(raw_rows, key=lambda r: r["rev"])
    if not rows:
        # 没有任何 checkpoint 行，无从校验，直接判失败
        return False
    prev = ""
    objects = extras.objects
    for row in rows:
        # 按 rev 顺序回读每行内容，重算 hash 链，任一环断裂即校验失败
        try:
            data = objects.get(row["state_ref"])
        except Exception:  # noqa: BLE001 - 对象引用失效/篡改 → hash 链校验失败（返回 False）
            return False
        state_hash = hashlib.sha256(data).hexdigest()
        expected = hashlib.sha256((prev + state_hash).encode("utf-8")).hexdigest()
        if row["prev_hash"] != expected:
            # 记录值与重算值不符，说明状态被篡改或落盘不一致
            return False
        prev = row["prev_hash"]
    return True
