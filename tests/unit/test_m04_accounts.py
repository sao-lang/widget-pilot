"""M04 账号与租户隔离单测（§8.2 A1/A2 前置：注册/登录/JWT/权限裁剪）。

TDD 红灯先行：断言 AuthService 与 TenantGuard 的契约行为，
这些行为在 M04 §1.2 R1/R2/R3、§4.1 auth 端点、§7 越权控制点定义。
"""

from __future__ import annotations

import pytest

from packages.contracts.enums import UserRole
from services.control_plane.accounts.service import (
    AuthError,
    AuthService,
    DuplicateEmailError,
)
from services.control_plane.accounts.stores import InMemoryUserStore


@pytest.fixture
def auth() -> AuthService:
    """独立 auth 服务：内存用户表 + 固定密钥（测试专用，非生产密钥）。"""
    return AuthService(
        InMemoryUserStore(),
        jwt_secret="test-secret-not-production-32bytes-minimum",
    )


class TestRegisterLogin:
    """注册与登录：只存 hash、角色默认 user、重复邮箱拒绝。"""

    def test_register_returns_tokens_and_user(self, auth: AuthService) -> None:
        """注册成功返回 access/refresh 双令牌 + 用户信息（R1）。"""
        result = auth.register(
            "alice@example.com",
            "correct-horse",
        )
        assert result.access_token
        assert result.refresh_token
        assert result.user.email == "alice@example.com"
        assert result.user.role is UserRole.USER
        assert result.user.share_enabled is True

    def test_password_stored_as_hash_not_plaintext(self, auth: AuthService) -> None:
        """用户存储只含 password_hash，明文密码不可回读（§7 合规）。"""
        auth.register("bob@example.com", "s3cret-pass")
        stored = auth.store.find_by_email("bob@example.com")
        assert stored is not None
        assert stored.password_hash != "s3cret-pass"
        assert "s3cret-pass" not in stored.password_hash

    def test_duplicate_email_rejected(self, auth: AuthService) -> None:
        """重复邮箱注册抛 DuplicateEmailError（409 前置，§4.1 register 409）。"""
        auth.register("dup@example.com", "pass-one")
        with pytest.raises(DuplicateEmailError):
            auth.register("dup@example.com", "pass-two")

    def test_login_wrong_password_rejected(self, auth: AuthService) -> None:
        """密码错误抛 AuthError（401 前置，§4.1 login 401）。"""
        auth.register("carol@example.com", "right-pass")
        with pytest.raises(AuthError):
            auth.login("carol@example.com", "wrong-pass")

    def test_login_unknown_email_rejected(self, auth: AuthService) -> None:
        """未知邮箱同样抛 AuthError（不泄漏账号是否存在）。"""
        with pytest.raises(AuthError):
            auth.login("nobody@example.com", "any-pass")

    def test_login_corrupt_hash_rejected(self, auth: AuthService) -> None:
        """S3 回归：DB 中 hash 损坏时 login 抛 AuthError，不泄漏 500。"""
        from services.control_plane.accounts.stores import UserRow

        auth.store.insert(
            UserRow(
                id="corrupt-user",
                email="corrupt@example.com",
                password_hash="not-a-valid-argon2-hash",
            )
        )
        with pytest.raises(AuthError):
            auth.login("corrupt@example.com", "any-pass")


class TestJWT:
    """JWT 签发与校验：携带 user_id/role，失效拒绝。"""

    def test_access_token_carries_user_id_and_role(self, auth: AuthService) -> None:
        """解码 access token 含 user_id 与 role（§3.1 AuthService 不变量）。"""
        result = auth.register("dave@example.com", "pass-phrase")
        claims = auth.decode_access(result.access_token)
        assert claims.user_id == result.user.id
        assert claims.role is UserRole.USER

    def test_tampered_token_rejected(self, auth: AuthService) -> None:
        """被篡改的 token 解码抛 AuthError（§7 token 伪造防护）。"""
        result = auth.register("erin@example.com", "pass-phrase")
        tampered = result.access_token[:-2] + "xx"
        with pytest.raises(AuthError):
            auth.decode_access(tampered)

    def test_refresh_yields_new_access(self, auth: AuthService) -> None:
        """refresh token 换发新 access token（Q3 refresh 路径）。"""
        result = auth.register("frank@example.com", "pass-phrase")
        new_access = auth.refresh(result.refresh_token)
        claims = auth.decode_access(new_access)
        assert claims.user_id == result.user.id

    def test_refresh_rejects_deleted_user(self, auth: AuthService) -> None:
        """B3 回归：用户被删后 refresh 必须失败，不得凭旧 role 签发令牌（§7）。"""
        result = auth.register("deleted@example.com", "pass-phrase")
        # 模拟账号删除：清内存存储（测试直接操作桩，生产为 PG DELETE）
        auth.store._by_id.pop(result.user.id, None)  # type: ignore[attr-defined]
        auth.store._by_email.pop("deleted@example.com", None)  # type: ignore[attr-defined]
        with pytest.raises(AuthError):
            auth.refresh(result.refresh_token)
