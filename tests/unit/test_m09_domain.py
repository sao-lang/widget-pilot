"""M09 域纯函数红灯测试（hash 链 / 渲染协议 / 版本链 / 状态机 / bridge）。

TDD RED：这些测试在 bundles 域实现前必须失败（ModuleNotFound），
失败原因=缺实现。实现后逐条转绿，对应设计文档 §8.2 A4/A6/A7/A8/A12/A14/A15/A17。
"""
from __future__ import annotations

from typing import cast

import pytest

from packages.contracts.enums import BundleState, Capability, GridSpec
from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.bundles.bridge import (
    BRIDGE_MESSAGES,
    assert_message_allowed,
    is_capability_allowed,
)
from services.control_plane.bundles.errors import (
    BundleConflictError,
    ConfirmRequiredError,
)
from services.control_plane.bundles.hash import compute_bundle_hash, verify_ref_hash
from services.control_plane.bundles.runtime import (
    build_capability_bridge,
    build_csp,
    card_px,
)
from services.control_plane.bundles.state import transition
from services.control_plane.bundles.versions import append_version, rollback

# ---------- hash 链（A4/A20） ----------

def test_compute_bundle_hash_is_deterministic() -> None:
    """同一 bundle 形态重算 hash 完全一致（复现指纹）。"""
    body = cast(JsonValue, {"bundle_id": "b1", "state": "GENERATED", "hash_seed": "x"})
    assert compute_bundle_hash(body) == compute_bundle_hash(body)


def test_compute_bundle_hash_changes_with_content() -> None:
    """内容变更 hash 必变（防篡改）。"""
    a = compute_bundle_hash({"bundle_id": "b1", "state": "GENERATED"})
    b = compute_bundle_hash({"bundle_id": "b1", "state": "INSTALLED"})
    assert a != b
    assert len(a) == 64


def test_verify_ref_hash_rejects_tampered_bytes() -> None:
    """引用带 hash，字节被替换则校验失败（A20）。"""
    from hashlib import sha256

    data = b"compiled-js-content"
    ref = f"code/s/r/compiled.js#sha256:{sha256(data).hexdigest()}"
    assert verify_ref_hash(ref, data) is True
    assert verify_ref_hash(ref, b"tampered") is False


# ---------- 渲染协议（A6/A7/A8/A17） ----------

@pytest.mark.parametrize(
    ("grid", "expected"),
    [
        (GridSpec._2X1, (160, 80)),
        (GridSpec._1X2, (80, 160)),
        (GridSpec._2X2, (160, 160)),
        (GridSpec._2X4, (160, 320)),
        (GridSpec._4X2, (320, 160)),
        (GridSpec._4X4, (320, 320)),
    ],
)
def test_card_px_real_ratio(grid: GridSpec, expected: tuple[int, int]) -> None:
    """六种规格按 cell=80px 真实比例映射，不统一拉伸（A17）。"""
    w, h = card_px(grid)
    assert (w, h) == expected


def test_csp_forbids_eval_external_and_network() -> None:
    """CSP 禁 eval / 外联脚本 / 网络（A7）。"""
    csp = build_csp()
    assert "unsafe-eval" not in csp
    assert "connect-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "https://" not in csp


def test_capability_bridge_is_intersection() -> None:
    """capability_bridge = manifest.capabilities ∩ 全局白名单（A6）。"""
    manifest = cast(JsonObject, {"capabilities": [
        Capability.DATE.value, Capability.TIMER.value, Capability.THEME.value
    ]})
    allowlist = {Capability.DATE, Capability.THEME}
    result = build_capability_bridge(manifest, allowlist)
    assert set(result) == {Capability.DATE, Capability.THEME}
    assert Capability.TIMER not in result


def test_bridge_messages_exactly_seven() -> None:
    """bridge_messages 恰 7 条固定消息（A8）。"""
    assert len(BRIDGE_MESSAGES) == 7
    assert set(BRIDGE_MESSAGES) == {
        "widget:ready",
        "widget:resize",
        "widget:error",
        "host:theme",
        "host:locale",
        "host:tick",
        "host:data",
    }


def test_assert_message_allowed_rejects_unknown() -> None:
    """未知消息名 / 非 7 条被拒（A8）。"""
    assert assert_message_allowed("widget:ready") is True
    assert assert_message_allowed("widget:arbitrary_rpc") is False


def test_is_capability_allowed_rejects_undeclared() -> None:
    """未声明能力调用被拒（A9）。"""
    bridge: set[Capability] = {Capability.DATE}
    assert is_capability_allowed(Capability.DATE, bridge) is True
    assert is_capability_allowed(Capability.TIMER, bridge) is False


# ---------- 版本链（A15） ----------

def test_append_and_rollback_reuse_previous_compiled_ref() -> None:
    """回滚沿用上一版 compiled_ref，不重新编译（A15）。"""
    versions = append_version((), "code/v1.js", "h1", "m1")
    assert len(versions) == 1
    assert versions[0].parent_id is None

    versions = append_version(versions, "code/v2.js", "h2", "m2")
    assert len(versions) == 2
    assert versions[1].parent_id == versions[0].version_id

    rolled = rollback(versions, None)
    assert len(rolled) == 3
    # 回滚沿用上一版（v1）的编译产物，不重新编译
    assert rolled[-1].compiled_ref == "code/v1.js"
    assert rolled[-1].compiled_hash == "h1"


# ---------- 状态机（A12/A13/A14） ----------

def test_install_is_idempotent() -> None:
    """重复 install 保持 INSTALLED（A12）。"""
    assert transition(BundleState.GENERATED, "install", None) is BundleState.INSTALLED
    assert transition(BundleState.INSTALLED, "install", None) is BundleState.INSTALLED


def test_delete_requires_confirm_token() -> None:
    """删除必须二次确认（A13）。"""
    with pytest.raises(ConfirmRequiredError):
        transition(BundleState.INSTALLED, "delete", None)
    assert transition(BundleState.INSTALLED, "delete", "valid-token") is BundleState.DELETED


def test_deleted_is_terminal() -> None:
    """DELETED 不可逆，install/delete 均 409（A14）。"""
    with pytest.raises(BundleConflictError):
        transition(BundleState.DELETED, "install", None)
    with pytest.raises(BundleConflictError):
        transition(BundleState.DELETED, "delete", "valid-token")
