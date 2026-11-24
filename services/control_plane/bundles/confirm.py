"""M09 删除二次确认 token（§4.1 Q4 / A13）。

无状态 HMAC 签名 token：payload = {u:user_id, b:bundle_id, exp:unix_ts}，
签名用服务端密钥（不与前端共享）。服务端签发后校验：验签 → 匹配 user/bundle →
检查过期。不存 Redis（无状态，可水平扩容）；篡改/过期/错配一律拒绝。
不用 JWT（D-5 禁引入框架），只用 stdlib hmac/hashlib/base64/json。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from services.control_plane.bundles.errors import ConfirmRequiredError

_CONFIRM_TTL_SECONDS: int = 120
"""confirm_token 有效期（§6.3 推导 TTL 120s）。"""
_SEPARATOR: str = "."
"""payload 与签名的分隔符。"""


@dataclass(slots=True, frozen=True)
class _ConfirmPayload:
    """token 内的结构化声明（明确字段静态声明，规则 4）。"""

    user_id: str
    bundle_id: str
    exp: int


def issue_confirm_token(
    *, secret: str, user_id: str, bundle_id: str, now: float | None = None
) -> tuple[str, str]:
    """签发二次确认 token，返回 (token, expires_at ISO)。

    Args:
        secret: 服务端密钥（生产 JWT_SECRET，缺则 fail-fast）。
        user_id: 归属用户（绑定进 token，防止跨用户复用）。
        bundle_id: 目标包（绑定进 token，防止跨包复用）。
        now: 当前 unix 秒（测试注入确定值；None 用系统时间）。

    Returns:
        (token, expires_at)：token 为 ``base64url(payload).hex(hmac)``。
    """
    ts = time.time() if now is None else now
    exp = int(ts) + _CONFIRM_TTL_SECONDS
    payload = _ConfirmPayload(user_id=user_id, bundle_id=bundle_id, exp=exp)
    raw = json.dumps(
        {"u": payload.user_id, "b": payload.bundle_id, "e": payload.exp},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(exp)) + "Z"
    return f"{body}{_SEPARATOR}{sig}", expires_at


def verify_confirm_token(
    *, secret: str, token: str, user_id: str, bundle_id: str, now: float | None = None
) -> None:
    """校验二次确认 token：验签 + 匹配 user/bundle + 未过期；失败抛 ConfirmRequiredError。

    Args:
        secret: 服务端密钥（须与签发一致）。
        token: 前端带来的 confirm_token。
        user_id: 请求用户（须与 token 内 u 一致）。
        bundle_id: 目标包（须与 token 内 b 一致）。
        now: 当前 unix 秒（测试注入；None 用系统时间）。

    Raises:
        ConfirmRequiredError: 格式非法 / 签名不符 / 用户或包错配 / 已过期。
    """
    # 先做 ASCII 白名单校验：body 为 base64url、sig 为 hex，均只含 ASCII。
    # 畸形非 ASCII 字符若在 hmac/compare_digest 处抛异常会冒泡为 500（应统一 400，
    # 契约 §5.4），故在入口折叠为 ConfirmRequiredError（第二轮 review 发现的缺陷）。
    if not token or not token.isascii():
        raise ConfirmRequiredError("confirm_token 含非法字符")
    try:
        body, sig = token.rsplit(_SEPARATOR, 1)
    except ValueError as exc:
        raise ConfirmRequiredError("confirm_token 格式非法") from exc
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    # 常量时间比较，防时序侧信道（此时 sig 已确认为 ASCII，不会抛 TypeError）。
    if not hmac.compare_digest(sig, expected):
        raise ConfirmRequiredError("confirm_token 签名不符")
    try:
        raw = base64.urlsafe_b64decode(body.encode("ascii"))
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfirmRequiredError("confirm_token 载荷非法") from exc
    if not isinstance(claims, dict):
        raise ConfirmRequiredError("confirm_token 载荷非法")
    if claims.get("u") != user_id or claims.get("b") != bundle_id:
        raise ConfirmRequiredError("confirm_token 与用户/产物不匹配")
    exp = claims.get("e")
    if not isinstance(exp, int):
        raise ConfirmRequiredError("confirm_token 缺少有效期")
    ts = time.time() if now is None else now
    if int(ts) >= exp:
        raise ConfirmRequiredError("confirm_token 已过期")
