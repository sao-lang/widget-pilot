"""P2 #36：SessionQueryPort 生产实现（BundleService + AttachmentStore）。

全局 REGISTRY 的 handler 是进程级单例，不能持有 per-request user_id；本实现
把 user_id 作为方法参数，由 capabilities.py 的 handler 从请求 ContextVar 读取
后显式传入。所有查询强制带 user_id（租户隔离），绝不信任模型 args。
"""
from __future__ import annotations

from packages.contracts.types import JsonObject
from services.control_plane.attachments.ports import AttachmentStorePort
from services.control_plane.bundles.ports import BundleStorePort


class BundleSessionQuery:
    """tool.session.* 的真实查询后端（M09 bundle store + M05 附件 store）。

    直接走 store（get/list_by_session 都强制带 user_id 做精确过滤），不经过
    BundleService 的业务包装——能力层只读，不需要 confirm/审计/成包逻辑。
    """

    def __init__(
        self, bundles: BundleStorePort, attachments: AttachmentStorePort
    ) -> None:
        self._bundles = bundles
        self._attachments = attachments

    def list_session_widgets(
        self, user_id: str, session_id: str, limit: int
    ) -> list[JsonObject]:
        """会话内 widget（租户隔离、created_at 降序），由 store 保证。"""
        return self._bundles.list_by_session(user_id, session_id, None, limit)

    def get_widget_detail(self, user_id: str, bundle_id: str) -> JsonObject | None:
        """单 widget 详情；store 按 (bundle_id, user_id) 精确过滤，越权返回 None。"""
        return self._bundles.get(bundle_id, user_id)

    def list_session_attachments(
        self, user_id: str, session_id: str
    ) -> list[JsonObject]:
        """会话内附件（租户隔离），行 → JsonObject（枚举值收窄为 str）。"""
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "session_id": row.session_id,
                "kind": str(row.kind),
                "mime": row.mime,
                "size": row.size,
                "hash": row.hash,
                "role": str(row.role) if row.role is not None else None,
                "insight_ref": row.insight_ref,
            }
            for row in self._attachments.list_by_session(user_id, session_id)
        ]
