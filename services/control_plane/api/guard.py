"""M04 TenantGuard：user_id 租户隔离（§3.1 / §7 控制点）。

贯穿每个 REST 请求与每条 SSE 事件；归属不等一律抛 ForbiddenError（403），
且错误信息不泄漏资源是否存在（防枚举）。
"""

from __future__ import annotations


class ForbiddenError(Exception):
    """越权访问他人资源（对应 HTTP 403 + FORBIDDEN 业务码，M02 Q1 定稿）。"""


class TenantGuard:
    """租户归属校验层（无状态，纯函数式调用）。"""

    @staticmethod
    def assert_owner(user_id: str, resource_user_id: str) -> None:
        """校验请求者是否为资源归属者，否则抛 ForbiddenError。

        Args:
            user_id: 当前请求的 JWT 中的 user_id（服务端解析，不信前端声明）。
            resource_user_id: 被访问资源记录的 user_id。

        Raises:
            ForbiddenError: 归属不等。消息固定文案，不含资源 id，防泄漏存在性。
        """
        # 固定拒绝文案：不拼接 resource_user_id，避免泄漏「资源存在」或具体内容
        if user_id != resource_user_id:
            raise ForbiddenError("access denied")
