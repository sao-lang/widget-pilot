"""M05 生产租户守卫：用 M04 SessionStore 查 session 归属（§4.3 assert_owner）。

与 FakeTenantGuard 同接口（assert_owner(user_id, session_id)），越权抛
PermissionError（API 层映射 403），不泄漏会话是否存在（§7）。
"""

from __future__ import annotations

from services.control_plane.sessions.stores import SessionStore


class SessionTenantGuard:
    """基于真实 SessionStore 的归属校验（生产实现）。"""

    def __init__(self, sessions: SessionStore) -> None:
        """注入 M04 会话存储。"""
        self._sessions = sessions

    def assert_owner(self, user_id: str, session_id: str) -> None:
        """查 session.user_id == 请求 user_id；不等或不存在一律 PermissionError。"""
        row = self._sessions.get(session_id)
        if row is None or row.user_id != user_id:
            raise PermissionError("FORBIDDEN: session not owned by user")
