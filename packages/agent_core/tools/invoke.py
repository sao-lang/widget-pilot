"""M03 工具调用五步（§11.6 invoke() 逐字 / §4.1 L10）。

① REGISTRY[d.tool] 未注册直接拒绝 → ② args_model.model_validate 严格校验 →
③ policy.check(d.surface, spec) 确定性授权 → ④ handler(args) →
⑤ truncate(result, max_output_chars)。

错误分类：未注册 / 参数校验 / 授权拒绝 = 业务失败；handler 抛的网络/超时 =
基础设施故障（由 handler 抛 CoreError.infra 归类）。
"""
from __future__ import annotations

from pydantic import ValidationError

from packages.agent_core.errors import CoreError
from packages.agent_core.tools.registry import REGISTRY
from packages.agent_core.types import ToolResult


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """截断输出，返回 (文本, 是否截断)。"""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def policy_check(surface: str, spec) -> None:
    """§11.11：越权调用在策略层被拒（确定性授权，不靠提示）。"""
    if surface not in spec.surfaces:
        raise CoreError.business(
            "CAPABILITY_DENIED",
            f"surface {surface!r} not allowed for tool {spec.name!r}",
        )


def invoke(d) -> ToolResult:
    """§11.6 逐字五步。"""
    # ① 未注册直接拒绝
    spec = REGISTRY.get(d.tool)
    if spec is None:
        raise CoreError.business("CAPABILITY_DENIED", f"unregistered tool: {d.tool!r}")
    # ② Pydantic 严格校验参数
    try:
        args = spec.args_model.model_validate(d.args)
    except ValidationError as e:
        raise CoreError.business("INPUT_INVALID", f"invalid args for {d.tool!r}: {e}") from e
    # ③ 确定性授权
    policy_check(d.surface, spec)
    # ④ 执行（handler 抛 CoreError.infra → 基础设施故障，直接上抛）
    result = spec.handler(args)
    # ⑤ 截断
    text = result if isinstance(result, str) else str(result)
    clipped, was_truncated = truncate(text, spec.max_output_chars)
    return ToolResult(ok=True, output=clipped, truncated=was_truncated)
