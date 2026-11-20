"""need_skill 结构化解析与四项校验（§3.4 D / §11.6）。

【v1.5】M03 未提供 parse_need_skill，本模块自实现；深度恒 1、白名单、
单轮次数与 token 上限任一不满足即拒（A13）。不新增常驻工具。
"""

from __future__ import annotations

import json

from services.guardrails.constants import (
    NEED_SKILL_DEPTH,
)
from services.guardrails.types import (
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
    """need_skill 四项校验——ADR-003 下为通用能力校验的特例包装。

    旧 depth=1 请求技能正文，映射到统一协议的 level=2（L2 正文）；depth≠1
    映射为非法 level，由统一闸门拒绝。校验逻辑只在 capability_guard 一处。

    Args:
        req: 结构化请求。
        turn: 单轮计数状态（只读判定，计数由装配层累加）。
        allowlist: 允许披露的 recipe_id 白名单。
        token_cost: 本次请求预计 token。

    Returns:
        NeedSkillResult；ok=False 时 decision=REJECT 且 reason 非空。
    """
    from services.guardrails.capability_guard import check_capability_request
    from services.guardrails.types import CapabilityRequest

    invalid_level = 3  # 超出 Literal[1,2] 的哨兵值，触发统一闸门拒绝
    level = 2 if req.depth == NEED_SKILL_DEPTH else invalid_level
    result = check_capability_request(
        CapabilityRequest(req.recipe_id, level, "generation", req.reason),
        turn,
        allowlist,
        token_cost,
    )
    return NeedSkillResult(result.ok, result.decision, result.reason)
