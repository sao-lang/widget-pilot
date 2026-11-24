"""M09 BundleStorePort 的 PostgreSQL 生产实现（§6.1 widget_bundle / bundle_version 表）。

大对象不入库：spec 以 canonical JSON 存 spec_ref（对象 key/内联快照），源码/
编译产物/日志/快照只存引用；manifest / static_check / sandbox_test / versions
为 JSONB。按 user_id 隔离，越权一律返回 None（不泄漏存在性）。PG 失败抛异常
fail-closed，绝不降级内存。
"""
from __future__ import annotations

import json
from typing import cast

from sqlalchemy import CursorResult, select, update

from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.bundles.domain import VersionNode
from services.control_plane.bundles.hash import canonical_json
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import BundleVersion, WidgetBundle


def _content_ref(bundle: JsonObject, key: str) -> str:
    """动态边界收窄（规则 6）：取 bundle 内嵌对象的 content_ref 字段（源码/编译产物引用）。"""
    nested = bundle.get(key)
    if not isinstance(nested, dict):
        return ""
    value = nested.get("content_ref")
    return value if isinstance(value, str) else ""


class PgBundleStore:
    """PG 产物索引（实现 BundleStorePort，生产唯一实现）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短 Session）。"""
        self._factory = make_session_factory(dsn)

    def insert(self, bundle: JsonObject, versions: tuple[VersionNode, ...]) -> None:
        """写入成包记录及版本节点（bundle_id 唯一，重复抛 IntegrityError）。"""
        with session_scope(self._factory) as session:
            session.add(
                WidgetBundle(
                    bundle_id=str(bundle["bundle_id"]),
                    user_id=str(bundle["user_id"]),
                    session_id=str(bundle["session_id"]),
                    spec_ref=canonical_json(bundle.get("spec", {})),
                    source_ref=_content_ref(bundle, "source"),
                    compiled_ref=_content_ref(bundle, "compiled"),
                    manifest=cast(JsonValue, bundle.get("manifest", {})),
                    static_check=cast(JsonValue, bundle.get("static_check", {})),
                    sandbox_test=cast(JsonValue, bundle.get("sandbox_test", {})),
                    state=str(bundle["state"]),
                    hash=str(bundle["hash"]),
                    versions=cast(JsonValue, [self._version_json(v) for v in versions]),
                    evidence_ref=str(bundle.get("evidence_ref", "")),
                )
            )
            for version in versions:
                session.add(
                    BundleVersion(
                        version_id=version.version_id,
                        bundle_id=version.bundle_id,
                        parent_id=version.parent_id,
                        compiled_ref=version.compiled_ref,
                        compiled_hash=version.compiled_hash,
                        manifest_hash=version.manifest_hash,
                        created_at=version.created_at,
                    )
                )

    def get(self, bundle_id: str, user_id: str) -> JsonObject | None:
        """按 id + user_id 读取，非本人/不存在返回 None。"""
        with session_scope(self._factory) as session:
            row = session.execute(
                select(WidgetBundle).where(
                    WidgetBundle.bundle_id == bundle_id,
                    WidgetBundle.user_id == user_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return self._to_json(row)

    def list_by_user(
        self, user_id: str, state: str | None, limit: int
    ) -> list[JsonObject]:
        """按用户列包（可选状态过滤），不泄漏他人资源。"""
        with session_scope(self._factory) as session:
            stmt = select(WidgetBundle).where(WidgetBundle.user_id == user_id)
            if state is not None:
                stmt = stmt.where(WidgetBundle.state == state)
            stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [self._to_json(row) for row in rows]

    def update_state(self, bundle_id: str, user_id: str, state: str) -> bool:
        """幂等更新状态，返回是否发生实际变更（rowcount>0）。"""
        with session_scope(self._factory) as session:
            # Core UPDATE 的 rowcount 是数据库原始命中数（幂等判定依据）；
            # Session.execute 静态返回 Result，收窄为 CursorResult（同 sessions/pg_stores 惯例）。
            result = cast(
                CursorResult[object],  # purity-exempt: SQLAlchemy Core update 结果泛型形参，非领域对象类型（同 sessions/pg_stores.py 惯例）
                session.execute(
                    update(WidgetBundle)
                    .where(
                        WidgetBundle.bundle_id == bundle_id,
                        WidgetBundle.user_id == user_id,
                    )
                    .where(WidgetBundle.state != state)
                    .values(state=state)
                ),
            )
            return bool(result.rowcount and result.rowcount > 0)

    def replace_versions(
        self, bundle_id: str, user_id: str, versions: tuple[VersionNode, ...]
    ) -> None:
        """覆盖版本链（JSONB 列）+ 追加新版本节点行。"""
        payload = cast(JsonValue, [self._version_json(v) for v in versions])
        with session_scope(self._factory) as session:
            session.execute(
                update(WidgetBundle)
                .where(
                    WidgetBundle.bundle_id == bundle_id,
                    WidgetBundle.user_id == user_id,
                )
                .values(versions=payload)
            )
            existing = set(
                session.execute(
                    select(BundleVersion.version_id).where(
                        BundleVersion.bundle_id == bundle_id
                    )
                ).scalars().all()
            )
            for version in versions:
                if version.version_id in existing:
                    continue
                session.add(
                    BundleVersion(
                        version_id=version.version_id,
                        bundle_id=version.bundle_id,
                        parent_id=version.parent_id,
                        compiled_ref=version.compiled_ref,
                        compiled_hash=version.compiled_hash,
                        manifest_hash=version.manifest_hash,
                        created_at=version.created_at,
                    )
                )

    def _to_json(self, row: WidgetBundle) -> JsonObject:
        """ORM 行 → service 消费的 bundle 视图（spec 从 spec_ref 反序列化）。"""
        spec: JsonValue = {}
        try:
            spec = cast(JsonValue, json.loads(row.spec_ref))
        except (ValueError, TypeError):
            spec = {}
        return {
            "bundle_id": row.bundle_id,
            "user_id": row.user_id,
            "session_id": row.session_id,
            "spec": spec,
            "source": {"path": "widget.tsx", "content_ref": row.source_ref, "language": "tsx"},
            "compiled": {"content_ref": row.compiled_ref, "hash": self._current_compiled_hash(row.versions)},
            "manifest": cast(JsonValue, row.manifest),
            "static_check": cast(JsonValue, row.static_check),
            "sandbox_test": cast(JsonValue, row.sandbox_test),
            "state": row.state,
            "hash": row.hash,
            "versions": cast(JsonValue, row.versions),
            "evidence_ref": row.evidence_ref,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }

    @staticmethod
    def _current_compiled_hash(versions: JsonValue) -> str:
        """从版本链末节点取当前 compiled_hash（回滚后末节点即当前编译产物，S1）。"""
        if not isinstance(versions, list) or not versions:
            return ""
        head = versions[-1]
        if isinstance(head, dict):
            value = head.get("compiled_hash")
            return value if isinstance(value, str) else ""
        return ""

    def _version_json(self, node: VersionNode) -> JsonObject:
        """版本节点 → JSONB 可存形态。"""
        return {
            "version_id": node.version_id,
            "bundle_id": node.bundle_id,
            "parent_id": node.parent_id or "",
            "compiled_ref": node.compiled_ref,
            "compiled_hash": node.compiled_hash,
            "manifest_hash": node.manifest_hash,
            "created_at": node.created_at,
        }
