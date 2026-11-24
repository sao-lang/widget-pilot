"""M09 hash 绑定链（§3.4 A / §6.4 / A4/A20）。

三条链：spec_hash = sha256(canonical_json(spec))；compiled.hash = sha256(js bytes)；
bundle.hash = sha256(canonical_json(bundle_without_hash))。读取任一引用时校验 hash，
不一致拒绝采用（防替换）。hash 算法用 stdlib，不引入第三方（D-5 零 Agent 框架）。
"""
from __future__ import annotations

import json
from hashlib import sha256

from packages.contracts.types import JsonValue

_REF_SEPARATOR = "#sha256:"
"""对象引用 hash 分隔符（与 M08 output_ports.make_object_ref 对齐）。"""


def canonical_json(value: JsonValue) -> str:
    """稳定序列化：键排序、无多余空白、UTF-8 原样（与 M08 同口径）。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes) -> str:
    """字节摘要（sha256 hex），spec / 引用 / 整包共用。"""
    return sha256(data).hexdigest()


def compute_bundle_hash(bundle_without_hash: JsonValue) -> str:
    """整包摘要：对去掉 hash 字段的 bundle 形态做 canonical_json 后 sha256。

    Args:
        bundle_without_hash: 不含 ``hash`` 键的产物形态（调用方负责剔除）。

    Returns:
        64 位 hex 摘要（§6.4 整包 hash，绑定 manifest/spec_hash/compiled.hash）。
    """
    return sha256_hex(canonical_json(bundle_without_hash).encode("utf-8"))


def compute_spec_hash(spec: JsonValue) -> str:
    """规格摘要：sha256(canonical_json(WidgetSpec))（§3.4 A）。"""
    return sha256_hex(canonical_json(spec).encode("utf-8"))


def make_object_ref(key: str, data: bytes) -> str:
    """生成带 hash 的对象引用（key#sha256:hex）。"""
    return f"{key}{_REF_SEPARATOR}{sha256_hex(data)}"


def parse_object_ref(ref: str) -> tuple[str, str]:
    """拆分引用为 (key, expected_hash)；格式非法 fail-closed（A20）。"""
    key, separator, digest = ref.partition(_REF_SEPARATOR)
    if not separator or not key or len(digest) != 64:
        raise ValueError(f"invalid object ref: {ref[:80]}")
    return key, digest


def verify_ref_hash(ref: str, data: bytes) -> bool:
    """校验对象字节与引用内 hash 一致；篡改返回 False（A20）。"""
    _key, expected = parse_object_ref(ref)
    return sha256_hex(data) == expected
