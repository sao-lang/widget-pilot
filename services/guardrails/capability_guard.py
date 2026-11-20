"""M06 能力请求确定性校验（ADR-003 / §3.4 D / §11.6）。

tool 与 skill 统一走这一道闸门：白名单（已注册）/ surface 授权 / level 合法 /
单轮次数 / 累计 token。拒绝类判定仍由 L1 规则裁定，不靠模型自报。
"""
from __future__ import annotations

import json

from services.guardrails.constants import (
    MAX_NEED_SKILL_PER_TURN,
    MAX_NEED_SKILL_TOKENS,
)
from services.guardrails.types import (
    CapabilityCheckResult,
    CapabilityRequest,
    HitDecision,
    TurnState,
)

_VALID_LEVELS: frozenset[int] = frozenset({1, 2})


def parse_capability_request(raw: str) -> CapabilityRequest:
    """把模型侧结构化输出解析为 CapabilityRequest。

    Args:
        raw: JSON 字符串（capability_id / level / surface / reason）。

    Returns:
        CapabilityRequest。

    Raises:
        ValueError: 非法 JSON 或缺必填字段（对外统一为解析失败）。
    """
    # 对外统一抛 ValueError（解析失败契约），抑制 TRY004：这是不可信输入契约校验
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("capability request 必须是 JSON 对象")  # noqa: TRY004
    capability_id = data.get("capability_id")
    level = data.get("level")
    if not isinstance(capability_id, str) or not isinstance(level, int):
        raise ValueError("capability request 缺 capability_id 或 level")  # noqa: TRY004
    surface = data.get("surface", "generation")
    if not isinstance(surface, str):
        raise ValueError("capability request 的 surface 必须是字符串")  # noqa: TRY004
    return CapabilityRequest(capability_id, level, surface, str(data.get("reason", "")))


def check_capability_request(
    req: CapabilityRequest,
    turn: TurnState,
    allowlist: frozenset[str],
    token_cost: int = 0,
    allowed_surfaces: frozenset[str] = frozenset({"generation"}),
) -> CapabilityCheckResult:
    """五项校验：白名单 / surface / level / 单轮次数 / 累计 token。

    Args:
        req: 结构化请求。
        turn: 单轮计数状态（只读判定，计数由装配层累加）。
        allowlist: 允许加载的 capability_id 集合。
        token_cost: 本次请求预计 token。
        allowed_surfaces: 本闸门允许的 surface 集合。

    Returns:
        CapabilityCheckResult；ok=False 时 decision=REJECT 且 reason 非空。
    """
    if req.capability_id not in allowlist:
        return CapabilityCheckResult(False, HitDecision.REJECT, "capability_id 不在白名单")
    if req.surface not in allowed_surfaces:
        return CapabilityCheckResult(False, HitDecision.REJECT, "surface 未授权")
    if req.level not in _VALID_LEVELS:
        return CapabilityCheckResult(False, HitDecision.REJECT, "level 必须为 1 或 2")
    if turn.need_skill_count >= MAX_NEED_SKILL_PER_TURN:
        return CapabilityCheckResult(False, HitDecision.REJECT, "本轮能力请求次数超限")
    if turn.need_skill_tokens + token_cost > MAX_NEED_SKILL_TOKENS:
        return CapabilityCheckResult(False, HitDecision.REJECT, "本轮能力请求 token 超限")
    return CapabilityCheckResult(ok=True)
