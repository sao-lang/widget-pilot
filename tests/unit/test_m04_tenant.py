"""M04 租户隔离单测（§8.2 A1/A2：跨用户读会话/SSE 一律 403）。

TenantGuard 是 §3.1 的 user_id 过滤层，贯穿 REST 与每条 SSE 事件。
越权一律抛 ForbiddenError，不泄漏资源是否存在（§7 控制点）。
"""

from __future__ import annotations

import pytest

from services.control_plane.api.guard import ForbiddenError, TenantGuard


class TestTenantGuard:
    """user_id 过滤：本人放行，他人拒绝，错误信息不泄漏资源存在性。"""

    def test_owner_passes(self) -> None:
        """归属一致时静默通过（正常路径）。"""
        TenantGuard.assert_owner("user-a", "user-a")

    def test_cross_user_read_raises_forbidden(self) -> None:
        """用户 B 读用户 A 的资源抛 ForbiddenError（A1：403）。"""
        with pytest.raises(ForbiddenError):
            TenantGuard.assert_owner("user-b", "user-a")

    def test_forbidden_message_does_not_leak_existence(self) -> None:
        """拒绝信息不得包含资源 id 或"存在/不存在"线索（§7 防泄漏）。"""
        with pytest.raises(ForbiddenError) as exc_info:
            TenantGuard.assert_owner("user-b", "secret-session-id-123")
        assert "secret-session-id-123" not in str(exc_info.value)
