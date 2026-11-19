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
from argon2.exceptions import InvalidHashError, VerificationError

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
        """注册用户：只存 hash，签发双令牌；重复邮箱抛 DuplicateEmailError。

        Args:
            email: 登录名，统一转小写（citext 语义，大小写不敏感）。
            password: 明文密码，仅存 argon2 hash，不落明文、不进日志。

        Returns:
            AuthResult（access + refresh 双令牌 + 用户行）。

        Raises:
            DuplicateEmailError: email 已存在（PG UNIQUE 冲突映射，对应 409）。
        """
        normalized = email.lower()  # citext 语义：登录名大小写不敏感
        row = UserRow(
            id=str(uuid4()),
            email=normalized,
            password_hash=self._hasher.hash(password),
        )
        self.store.insert(row)  # 唯一索引冲突由 store 抛 DuplicateEmailError
        return self._issue_pair(row)

    def login(self, email: str, password: str) -> AuthResult:
        """校验凭据：密码错误或用户不存在统一抛 AuthError（不泄漏账号存在性）。

        Args:
            email: 登录名（转小写后查）。
            password: 明文密码，经 argon2 verify 比对（不匹配/坏 hash 均折叠为同一错误）。

        Returns:
            AuthResult（新签发的双令牌 + 用户行）。

        Raises:
            AuthError: 账号不存在、密码错误、或 hash 损坏（统一文案防用户枚举）。
        """
        row = self.store.find_by_email(email.lower())
        # 失败路径统一：不区分「用户不存在」与「密码错误」，防用户枚举
        if row is None:
            raise AuthError("invalid credentials")
        try:
            self._hasher.verify(row.password_hash, password)
        except (VerificationError, InvalidHashError) as exc:
            # S3：验证码不匹配、hash 损坏（非 VerificationError 子类）统一折叠，
            # 避免脏 hash 泄漏 500；仍不区分「用户不存在」，防枚举
            raise AuthError("invalid credentials") from exc
        return self._issue_pair(row)

    def refresh(self, refresh_token: str) -> str:
        """用 refresh token 换发新 access token（M02 Q3 路径）。

        B3 修复：用户必须仍存在才签发；角色取 DB 最新值而非 token claims，
        避免删除/降权后凭旧 role 换发越权令牌（§7 服务端权限裁剪）。

        Args:
            refresh_token: 长时刷新令牌原文（token_type 必须为 refresh）。

        Returns:
            新签发的 access token（15min TTL，角色取 DB 最新值）。

        Raises:
            AuthError: 签名/类型错误，或用户已删除（凭旧角色刷新被拒）。
        """
        claims = self._decode(refresh_token)
        if claims.token_type != TOKEN_TYPE_REFRESH:
            raise AuthError("wrong token type")  # 防 access 当 refresh 用
        user = self.store.find_by_id(claims.user_id)
        # 用户已删除/不存在 → 拒绝（不再用 _claims_to_row 兜底旧角色）
        if user is None:
            raise AuthError("invalid token")
        return self._issue_access(user)  # 用 DB 最新 role，不用 claims.role

    def decode_access(self, token: str) -> TokenClaims:
        """解码并校验 access token；类型/签名不符抛 AuthError。

        Args:
            token: 客户端 Bearer token 原文。

        Returns:
            TokenClaims（user_id / role / token_type）；role 为非法值时回退 user。

        Raises:
            AuthError: 签名错误、过期、或 token_type 非 access（防 refresh 当 access 用）。
        """
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
        """统一签名入口：sub=user_id、role、type、exp 由 TTL 计算。

        Args:
            row: 用户行（id/role 进 claims；refresh 时须为 DB 最新值）。
            token_type: access / refresh，写入 type claim 防混用。
            ttl_s: 有效期秒数（access 900 / refresh 604800）。
        """
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
