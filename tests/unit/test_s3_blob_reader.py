"""ADR-003 未完成项4：附件原文 reader 接 S3（生产装配）测试。"""
from __future__ import annotations

import hashlib

import pytest

from services.control_plane.attachments.s3_store import S3ObjectStore
from services.orchestrator.capabilities import make_s3_blob_reader


class FakeS3:
    """内存 S3 替身：只实现 reader 用到的两个方法。"""

    def __init__(self, data: dict[str, bytes]) -> None:
        self.data = data
        self.verify_calls: list[tuple[str, str]] = []

    def get_with_hash_verify(self, key: str, expected_hash: str) -> bytes:
        self.verify_calls.append((key, expected_hash))
        if key not in self.data:
            raise KeyError(key)
        if hashlib.sha256(self.data[key]).hexdigest() != expected_hash:
            raise ValueError("CONTRACT_MISMATCH: object hash mismatch")
        return self.data[key]


def test_reader_splits_ref_and_verifies_hash():
    """reader 拆 `key#sha256:hash`，按 key 读并校验 hash（防篡改）。"""
    payload = b"image-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    store = FakeS3({"att/u/s/a/original": payload})
    reader = make_s3_blob_reader(store)  # type: ignore[arg-type]
    assert reader(f"att/u/s/a/original#sha256:{digest}") == payload
    assert store.verify_calls == [("att/u/s/a/original", digest)]


def test_reader_rejects_missing_hash_fail_closed():
    """ref 不带 hash → 抛 ValueError，禁止无校验读取（fail-closed）。"""
    store = FakeS3({})
    reader = make_s3_blob_reader(store)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        reader("att/u/s/a/original")


def test_reader_propagates_tamper():
    """hash 不匹配 → 抛异常，不返回被篡改的原文。"""
    payload = b"original"
    store = FakeS3({"att/u/s/a/original": b"tampered"})
    reader = make_s3_blob_reader(store)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        reader(f"att/u/s/a/original#sha256:{hashlib.sha256(payload).hexdigest()}")


def test_s3_store_has_required_reader_methods():
    """真实 S3ObjectStore 具备 reader 依赖的两个方法（接口一致性）。"""
    assert callable(S3ObjectStore.get_with_hash_verify)
