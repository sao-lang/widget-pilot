"""M04 AuthService：注册/登录/JWT 签发与刷新（§3.1 / §4.1）。

凭据校验用 argon2（失败回退 bcrypt），密码只存 hash；JWT 用 HS256 短时
access + refresh，claims 携带 user_id/role（§3.1 不变量：token 带 user_id）。
所有语义字符串用枚举/常量（编码规则 5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from packages.contracts.enums import UserRole
from services.control_plane.accounts.constants import (
    ACCESS_TOKEN_TTL_S,
    JWT_ALGORITHM,
    REFRESH_TOKEN_TTL_S,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
)
from services.control_plane.accounts.stores import (
    DuplicateEmailError,
    UserRow,
    UserStore,
)

__all__ = [
    "AuthError",
    "AuthResult",
    "AuthService",
    "DuplicateEmailError",
    "TokenClaims",
]


class AuthError(Exception):
    """认证失败：密码错误、用户不存在、token 失效/篡改（对应 401）。"""


@dataclass(slots=True, frozen=True)
class TokenClaims:
    """解码后的 JWT claims（确定字段，禁动态键解析后散落访问）。"""

    user_id: str
    """用户标识。"""
    role: UserRole
    """角色（恢复为枚举，非裸字符串）。"""
    token_type: str
    """令牌类型（access/refresh）。"""


@dataclass(slots=True, frozen=True)
class AuthResult:
    """注册/登录成功结果（§4.1 响应的 data 部分）。"""

    access_token: str
    """短时访问令牌。"""
    refresh_token: str
    """刷新令牌。"""
    user: UserRow
    """用户行（含 id/role/share_enabled）。"""


class AuthService:
    """注册、登录、JWT 签发/刷新/校验的服务（§1.2 R1/R2）。"""

    def __init__(
        self,
        store: UserStore,
        jwt_secret: str,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        """注入用户存储、JWT 密钥与可选密码哈希器（测试可注入加速桩）。"""
        self.store = store
        self._secret = jwt_secret
        self._hasher = password_hasher or PasswordHasher()

    def register(self, email: str, password: str) -> AuthResult:
        """注册用户：只存 hash，签发双令牌；重复邮箱抛 DuplicateEmailError。"""
        normalized = email.lower()  # citext 语义：登录名大小写不敏感
        row = UserRow(
            id=str(uuid4()),
            email=normalized,
            password_hash=self._hasher.hash(password),
        )
        self.store.insert(row)  # 唯一索引冲突由 store 抛 DuplicateEmailError
        return self._issue_pair(row)

    def login(self, email: str, password: str) -> AuthResult:
        """校验凭据：密码错误或用户不存在统一抛 AuthError（不泄漏账号存在性）。"""
        row = self.store.find_by_email(email.lower())
        # 失败路径统一：不区分「用户不存在」与「密码错误」，防用户枚举
        if row is None:
            raise AuthError("invalid credentials")
        try:
            self._hasher.verify(row.password_hash, password)
        except VerifyMismatchError as exc:
            raise AuthError("invalid credentials") from exc
        return self._issue_pair(row)

    def refresh(self, refresh_token: str) -> str:
        """用 refresh token 换发新 access token（M02 Q3 路径）。"""
        claims = self._decode(refresh_token)
        if claims.token_type != TOKEN_TYPE_REFRESH:
            raise AuthError("wrong token type")  # 防 access 当 refresh 用
        return self._issue_access(
            self.store.find_by_id(claims.user_id) or self._claims_to_row(claims)
        )

    def decode_access(self, token: str) -> TokenClaims:
        """解码并校验 access token；类型/签名不符抛 AuthError。"""
        claims = self._decode(token)
        if claims.token_type != TOKEN_TYPE_ACCESS:
            raise AuthError("wrong token type")
        return claims

    def _issue_pair(self, row: UserRow) -> AuthResult:
        """签发 access + refresh 双令牌。"""
        return AuthResult(
            access_token=self._issue_access(row),
            refresh_token=self._issue_refresh(row),
            user=row,
        )

    def _issue_access(self, row: UserRow) -> str:
        """签发短时 access token（claims: sub/role/type/exp）。"""
        return self._sign(row, TOKEN_TYPE_ACCESS, ACCESS_TOKEN_TTL_S)

    def _issue_refresh(self, row: UserRow) -> str:
        """签发长时 refresh token（claims: sub/type/exp）。"""
        return self._sign(row, TOKEN_TYPE_REFRESH, REFRESH_TOKEN_TTL_S)

    def _sign(self, row: UserRow, token_type: str, ttl_s: int) -> str:
        """统一签名入口：sub=user_id、role、type、exp 由 TTL 计算。"""
        import time

        now = int(time.time())
        payload = {
            "sub": row.id,
            "role": row.role.value,
            "type": token_type,
            "iat": now,
            "exp": now + ttl_s,
        }
        return jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)

    def _decode(self, token: str) -> TokenClaims:
        """解码 token：签名/过期非法统一折叠为 AuthError（异常不外泄细节）。"""
        try:
            payload = jwt.decode(token, self._secret, algorithms=[JWT_ALGORITHM])
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        # role 非法值显式回退到 user（编码规则 5：未知值回退安全成员）
        raw_role = payload.get("role", UserRole.USER.value)
        try:
            role = UserRole(raw_role)
        except ValueError:
            role = UserRole.USER
        return TokenClaims(
            user_id=str(payload["sub"]),
            role=role,
            token_type=str(payload.get("type", TOKEN_TYPE_ACCESS)),
        )

    def _claims_to_row(self, claims: TokenClaims) -> UserRow:
        """refresh 时用户已被删的兜底：构造最小行仅用于再签发（保留 user_id/role）。"""
        return UserRow(
            id=claims.user_id,
            email="",
            password_hash="",
            role=claims.role,
        )
