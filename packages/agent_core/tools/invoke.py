"""M03 能力加载五步（ADR-003 / §11.6）。

① REGISTRY 未注册拒绝 → ② surface 确定性授权 → ③ level 分派（L1 详情 / L2）→
④ L2-tool 校验参数并执行 handler、L2-skill 取正文 → ⑤ 输出截断。

错误分类：未注册 / 参数校验 / 授权拒绝 = 业务失败；handler 抛的网络/超时 =
基础设施故障（由 handler 抛 CoreError.infra 归类）。
"""
from __future__ import annotations

from pydantic import ValidationError

from packages.agent_core.errors import CoreError
from packages.agent_core.tools.registry import REGISTRY, capability_detail
from packages.agent_core.types import Decision, ToolResult
from packages.contracts.enums import ReasonCode


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """截断输出，返回 (文本, 是否截断)。"""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def policy_check(surface: str, surfaces: frozenset[str], capability_id: str) -> None:
    """§11.11：越权调用在策略层被拒（确定性授权，不靠提示）。"""
    if surface not in surfaces:
        raise CoreError.business(
            ReasonCode.CAPABILITY_DENIED,
            f"surface {surface!r} not allowed for capability {capability_id!r}",
        )


def load(d: Decision) -> ToolResult:
    """§11.6 能力加载五步（d 为 LOAD_CAPABILITY 决策，ADR-003）。"""
    spec = REGISTRY.get(d.capability_id or "")
    if spec is None:
        raise CoreError.business(ReasonCode.CAPABILITY_DENIED, f"unregistered capability: {d.capability_id!r}")

    policy_check(d.surface, spec.surfaces, spec.id)

    # L1 只下发真实 schema/manifest，不执行、不加载正文（渐进披露的核心，S1）。
    if d.level == 1:
        import json

        detail = capability_detail(spec.id)
        text = json.dumps(
            {
                "id": detail["id"],
                "kind": detail["kind"],
                "args_schema": detail.get("args_schema"),
                "manifest": detail.get("manifest"),
            },
            ensure_ascii=False,
            default=str,
        )
        clipped, was_truncated = truncate(text, spec.max_output_chars)
        return ToolResult(ok=True, output=clipped, truncated=was_truncated)

    # L2：tool 执行 handler；skill 返回正文引用（当数据，非指令）。
    if spec.kind == "tool":
        if spec.args_model is None or spec.handler is None:
            raise CoreError.business(ReasonCode.CONTRACT_MISMATCH, f"tool capability misconfigured: {spec.id}")
        try:
            args = spec.args_model.model_validate(d.args)
        except ValidationError as e:
            raise CoreError.business(ReasonCode.INPUT_INVALID, f"invalid args for {spec.id!r}: {e}") from e
        try:
            result = spec.handler(args)
        except CoreError:
            raise  # 保留 handler 自己的业务/infra 分类
        except (ValueError, KeyError) as exc:
            # 附件引用缺失/无 hash/hash 不符：模型可自纠正的契约违规（业务失败）
            raise CoreError.business(
                ReasonCode.CONTRACT_MISMATCH, f"capability {spec.id!r} bad ref: {exc}"
            ) from exc
        except Exception as exc:  # 外部 handler 未知故障收口为 infra
            # S3/网络/超时等未知故障：基础设施类，交重试策略
            raise CoreError.infra(
                ReasonCode.SANDBOX_UNAVAILABLE, f"capability {spec.id!r} handler failed: {exc}"
            ) from exc
    elif spec.kind == "skill":
        result = f"body:{spec.payload_ref}" if spec.body is None else spec.body
    else:
        raise CoreError.business(ReasonCode.CONTRACT_MISMATCH, f"unknown capability kind: {spec.kind!r}")

    text = result if isinstance(result, str) else str(result)
    clipped, was_truncated = truncate(text, spec.max_output_chars)
    return ToolResult(ok=True, output=clipped, truncated=was_truncated)
