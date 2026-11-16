"""M03 幂等副作用表（§11.4 step 4 / §4.1 L20）。

idem_key = session_id + step + payload_hash（SQL 注释逐字，用 | 连接）。
执行外部副作用前 get_or_reserve：命中直接复用 result_ref，不重复执行。
"""
from __future__ import annotations

import hashlib

from packages.agent_core.injection import IdempotencyStore


def make_idem_key(session_id: str, step: str, payload_hash: str) -> str:
    return f"{session_id}|{step}|{payload_hash}"


def hash_payload(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def get_or_reserve(store: IdempotencyStore, idem_key: str, run_id: str) -> str | None:
    """命中返回 result_ref；否则占位并返 None（调用方执行后 commit）。"""
    return store.get_or_reserve(idem_key, run_id)
