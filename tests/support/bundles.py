"""M09 测试桩（仅供 tests/ 显式注入；生产源码禁止 import 本模块）。

包含内存 BundleStore、记录式 EventSink / CorpusLink / PreferenceRevocation、
Fake AuditLedger 复用 tests.support.control_plane 的实现。
"""
from __future__ import annotations

from packages.contracts.types import JsonObject
from services.control_plane.bundles.domain import VersionNode

__all__ = [
    "InMemoryBundleStore",
    "RecordingArtifactAdoption",
    "RecordingCorpusLink",
    "RecordingEventSink",
    "RecordingPreferenceRevocation",
]


class InMemoryBundleStore:
    """内存产物索引（dict[bundle_id, bundle] + 版本链），带 user_id 隔离。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self.rows: dict[str, JsonObject] = {}
        self.versions: dict[str, tuple[VersionNode, ...]] = {}

    def insert(self, bundle: JsonObject, versions: tuple[VersionNode, ...]) -> None:
        """写入成包记录（重复 id 抛 ValueError 模拟唯一约束）。"""
        bid = str(bundle["bundle_id"])
        if bid in self.rows:
            raise ValueError(f"duplicate bundle_id: {bid}")
        self.rows[bid] = dict(bundle)
        self.versions[bid] = versions

    def _with_versions(self, row: JsonObject) -> JsonObject:
        """与 PgBundleStore._to_json 同构：版本链从独立存储读回。"""
        result = dict(row)
        bid = str(row.get("bundle_id", ""))
        if bid in self.versions:
            result["versions"] = [
                {
                    "version_id": v.version_id,
                    "bundle_id": v.bundle_id,
                    "parent_id": v.parent_id or "",
                    "compiled_ref": v.compiled_ref,
                    "compiled_hash": v.compiled_hash,
                    "manifest_hash": v.manifest_hash,
                    "created_at": v.created_at,
                }
                for v in self.versions[bid]
            ]
        return result

    def get(self, bundle_id: str, user_id: str) -> JsonObject | None:
        """按 id 读取，非本人返回 None。"""
        row = self.rows.get(bundle_id)
        if row is None or row.get("user_id") != user_id:
            return None
        return self._with_versions(row)

    def list_by_user(
        self, user_id: str, state: str | None, limit: int
    ) -> list[JsonObject]:
        """按用户 + 状态过滤。"""
        rows = [
            dict(r)
            for r in self.rows.values()
            if r.get("user_id") == user_id
            and (state is None or r.get("state") == state)
        ]
        return rows[:limit]

    def list_by_session(
        self, user_id: str, session_id: str, state: str | None, limit: int
    ) -> list[JsonObject]:
        """按会话列包（租户隔离 + 状态过滤），created_at 降序。"""
        rows = [
            self._with_versions(r)
            for r in self.rows.values()
            if r.get("user_id") == user_id
            and r.get("session_id") == session_id
            and (state is None or r.get("state") == state)
        ]
        rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return rows[:limit]

    def update_state(self, bundle_id: str, user_id: str, state: str) -> bool:
        """更新状态；返回是否发生实际变更（幂等）。"""
        row = self.rows.get(bundle_id)
        if row is None or row.get("user_id") != user_id:
            return False
        if row.get("state") == state:
            return False
        row["state"] = state
        return True

    def replace_versions(
        self, bundle_id: str, user_id: str, versions: tuple[VersionNode, ...]
    ) -> None:
        """覆盖版本链（同步 bundle 行的 versions 字段，与 PgBundleStore 行为一致）。"""
        self.versions[bundle_id] = versions
        row = self.rows.get(bundle_id)
        if row is not None:
            row["versions"] = [
                {
                    "version_id": v.version_id,
                    "parent_id": v.parent_id or "",
                    "compiled_ref": v.compiled_ref,
                    "compiled_hash": v.compiled_hash,
                    "manifest_hash": v.manifest_hash,
                    "created_at": v.created_at,
                }
                for v in versions
            ]

    def update_content(
        self,
        bundle_id: str,
        user_id: str,
        bundle: JsonObject,
        versions: tuple[VersionNode, ...],
    ) -> bool:
        """重成包内存桩：仅更新已有行，不创建新行（与 Pg 同口径）。"""
        row = self.rows.get(bundle_id)
        if row is None or row.get("user_id") != user_id:
            return False
        row.update(bundle)
        self.replace_versions(bundle_id, user_id, versions)
        return True


class RecordingEventSink:
    """记录式事件 sink（内存数组），断言 artifact/notice/error 的 kind 与 payload。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[tuple[str, JsonObject]] = []

    def emit(self, kind: str, payload: JsonObject, session_id: str = "") -> None:
        """记录一条事件。"""
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        """返回所有事件的 kind。"""
        return [k for k, _ in self.events]


class RecordingCorpusLink:
    """记录式 M10 语料联动桩（断言 on_bundle_deleted 被调用）。"""

    def __init__(self) -> None:
        """初始化空调用记录。"""
        self.deleted: list[str] = []

    def on_bundle_deleted(self, bundle_id: str) -> None:
        """记录一次语料失效联动。"""
        self.deleted.append(bundle_id)


class RecordingPreferenceRevocation:
    """记录式 M07 撤销桩（断言按 bundle_id 撤销被调用）。"""

    def __init__(self) -> None:
        """初始化空调用记录。"""
        self.revoked: list[tuple[str, str, str]] = []

    def revoke_evidence_by_bundle(
        self, user_id: str, bundle_id: str, trace_id: str
    ) -> int:
        """记录一次精确撤销并返回条数（桩默认 1）。"""
        self.revoked.append((user_id, bundle_id, trace_id))
        return 1


class RecordingArtifactAdoption:
    """记录式 M07 采纳信号桩（断言 install 时 adopted_artifact 被调用）。"""

    def __init__(self) -> None:
        """初始化空调用记录。"""
        self.adopted: list[tuple[str, str, str | None, str, str, str]] = []

    def on_artifact_adopted(
        self,
        user_id: str,
        bundle_id: str,
        palette: str | None,
        recipe_id: str,
        widget_type: str,
        trace_id: str,
    ) -> None:
        """记录一次采纳信号。"""
        self.adopted.append(
            (user_id, bundle_id, palette, recipe_id, widget_type, trace_id)
        )
