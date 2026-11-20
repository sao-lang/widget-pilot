"""need_skill 结构化解析与四项校验（§3.4 D / §11.6）。

【v1.5】M03 未提供 parse_need_skill，本模块自实现；深度恒 1、白名单、
单轮次数与 token 上限任一不满足即拒（A13）。不新增常驻工具。
"""

from __future__ import annotations

import json

from services.guardrails.constants import (
    MAX_NEED_SKILL_PER_TURN,
    MAX_NEED_SKILL_TOKENS,
    NEED_SKILL_DEPTH,
)
from services.guardrails.types import (
    HitDecision,
    NeedSkillRequest,
    NeedSkillResult,
    TurnState,
)


def parse_need_skill(raw: str) -> NeedSkillRequest:
    """把模型侧结构化输出解析为 NeedSkillRequest。

    Args:
        raw: JSON 字符串（{"recipe_id", "depth", "reason"}）。

    Returns:
        NeedSkillRequest。

    Raises:
        ValueError: 非法 JSON 或缺少必填字段。
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        # 对外统一抛 ValueError（解析失败），抑制 TRY004：这是输入契约而非内部类型错误
        raise ValueError("need_skill 必须是 JSON 对象")  # noqa: TRY004
    recipe_id = data.get("recipe_id")
    depth = data.get("depth")
    if not isinstance(recipe_id, str) or not isinstance(depth, int):
        raise ValueError("need_skill 缺 recipe_id 或 depth")  # noqa: TRY004
    return NeedSkillRequest(
        recipe_id=recipe_id, depth=depth, reason=str(data.get("reason", ""))
    )


def check_need_skill(
    req: NeedSkillRequest,
    turn: TurnState,
    allowlist: frozenset[str],
    token_cost: int = 0,
) -> NeedSkillResult:
    """四项校验：白名单 / 深度=1 / 单轮次数 / 累计 token。

    Args:
        req: 结构化请求。
        turn: 单轮计数状态（只读判定，计数由装配层累加）。
        allowlist: 允许披露的 recipe_id 白名单。
        token_cost: 本次请求预计 token。

    Returns:
        NeedSkillResult；ok=False 时 decision=REJECT 且 reason 非空。
    """
    if req.recipe_id not in allowlist:
        return NeedSkillResult(False, HitDecision.REJECT, "recipe_id 不在白名单")
    if req.depth != NEED_SKILL_DEPTH:
        return NeedSkillResult(False, HitDecision.REJECT, "depth 必须为 1")
    if turn.need_skill_count >= MAX_NEED_SKILL_PER_TURN:
        return NeedSkillResult(False, HitDecision.REJECT, "本轮 need_skill 次数超限")
    if turn.need_skill_tokens + token_cost > MAX_NEED_SKILL_TOKENS:
        return NeedSkillResult(False, HitDecision.REJECT, "本轮 need_skill token 超限")
    return NeedSkillResult(ok=True)
