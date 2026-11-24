"""M09 git 式版本链（§3.4 F / §6.4 / A15）。

回滚只追加版本节点、沿用上一版 compiled_ref，**不重新编译、不调模型、不产新代码**。
版本链是独立维度，回滚不改 BundleState（§3.3 / Q12）。
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from services.control_plane.bundles.domain import VersionNode
from services.control_plane.bundles.errors import BundleConflictError


def _now() -> str:
    """当前 UTC ISO 时刻（版本节点时间戳）。"""
    return datetime.now(UTC).isoformat()


def append_version(
    versions: Sequence[VersionNode],
    compiled_ref: str,
    compiled_hash: str,
    manifest_hash: str,
    *,
    bundle_id: str = "",
    version_id: str | None = None,
) -> tuple[VersionNode, ...]:
    """追加一个版本节点（首节点 parent_id=None）。"""
    parent = versions[-1].version_id if versions else None
    node = VersionNode(
        version_id=version_id or uuid4().hex,
        bundle_id=bundle_id or (versions[-1].bundle_id if versions else ""),
        parent_id=parent,
        compiled_ref=compiled_ref,
        compiled_hash=compiled_hash,
        manifest_hash=manifest_hash,
        created_at=_now(),
    )
    return (*versions, node)


def rollback(
    versions: Sequence[VersionNode],
    target_version_id: str | None,
) -> tuple[VersionNode, ...]:
    """回滚到目标版本：沿用目标版 compiled_ref/hash，追加新节点（A15）。

    Args:
        versions: 当前版本链（至少 1 节点）。
        target_version_id: 目标版本；None 表示上一版（当前节点的前一节点）。

    Returns:
        追加回滚节点后的新链（父指针指向当前 head）。

    Raises:
        BundleConflictError: 链为空 / 目标不存在 / 无上一版可回滚。
    """
    if not versions:
        raise BundleConflictError("版本链为空，无法回滚")
    head = versions[-1]
    if target_version_id is None:
        if len(versions) < 2:
            raise BundleConflictError("无上一版可回滚")
        target = versions[-2]
    else:
        matches = [v for v in versions if v.version_id == target_version_id]
        if not matches:
            raise BundleConflictError(f"目标版本不存在: {target_version_id}")
        if matches[0] is head:
            raise BundleConflictError("不能回滚到当前版本")
        target = matches[0]
    # 不重新编译：直接复用目标版编译产物与 manifest 摘要，追加新节点。
    return append_version(
        versions,
        target.compiled_ref,
        target.compiled_hash,
        target.manifest_hash,
        bundle_id=head.bundle_id,
    )


def current_version(versions: Sequence[VersionNode]) -> VersionNode:
    """返回当前 head 节点；空链抛 BundleConflictError。"""
    if not versions:
        raise BundleConflictError("版本链为空")
    return versions[-1]
