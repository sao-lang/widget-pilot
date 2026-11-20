"""M03 统一能力注册（ADR-003 / §11.6）。

REGISTRY 启动期静态注册、freeze 后不可增删（§7.3 #1）。tool 与 skill 统一为
:class:`Capability`；L0 目录只暴露摘要，L1 详情与 L2 正文/执行按需披露。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from packages.agent_core.errors import CoreError
from packages.agent_core.types import JsonObject
from packages.contracts.enums import ReasonCode

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class Capability:
    """一个可发现、可加载的 agent 能力（tool 或 skill，ADR-003）。

    Attributes:
        id: 唯一句柄（tool.attachment.process / recipe.calendar.month.v3）。
        kind: 能力族；tool 的 L2 执行 handler，skill 的 L2 返回正文。
        brief: L0 一句话摘要（唯一常驻字段之一）。
        detail_ref: L1 详情引用（args schema / skill manifest）。
        payload_ref: L2 载荷引用（handler / 正文对象存储键）。
        surfaces: 允许调用的 surface 集合（确定性授权）。
        cost_hint: 预估 token 成本提示（L0 常驻，供模型选择）。
        args_model: L2-tool 的参数校验模型；skill 为 None。
        handler: L2-tool 的执行函数；skill 为 None。
        max_output_chars: L2 输出截断上限。
        body: L2-skill 的正文文本；tool 为 None（测试/内置规程用）。
    """

    id: str
    kind: Literal["tool", "skill"]
    brief: str
    detail_ref: str
    payload_ref: str
    surfaces: frozenset[str]
    cost_hint: int
    args_model: type[BaseModel] | None = None
    handler: Callable[..., object] | None = None
    max_output_chars: int = 4096
    body: str | None = None


REGISTRY: dict[str, Capability] = {}
_FROZEN = False


def register(name: str, spec: Capability) -> None:
    """启动期注册能力；冻结后调用抛 CoreError（运行时不可增删）。"""
    if _FROZEN:
        raise CoreError.business(ReasonCode.CAPABILITY_DENIED, "REGISTRY frozen: runtime mutation forbidden")
    REGISTRY[name] = spec


def freeze_registry() -> None:
    """冻结注册表，此后 register 一律拒绝，保证运行时拓扑固定（§7.3 #1）。"""
    global _FROZEN
    _FROZEN = True


def unfreeze_registry() -> None:
    """仅供测试解封；生产永不调用。"""
    global _FROZEN
    _FROZEN = False


def is_frozen() -> bool:
    """返回注册表当前是否已冻结，供装配期与测试自检状态。"""
    return _FROZEN


def level0() -> list[JsonObject]:
    """L0 目录视图：只含 id/kind/brief/cost_hint，不泄漏 schema/正文/handler。"""
    return [
        {"id": spec.id, "kind": spec.kind, "brief": spec.brief, "cost_hint": spec.cost_hint}
        for spec in REGISTRY.values()
    ]


def capability_detail(capability_id: str) -> JsonObject:
    """L1 详情视图：返回详情与载荷引用，不执行、不加载正文。

    Args:
        capability_id: 能力句柄。

    Returns:
        L1 详情字典（id/kind/detail_ref/payload_ref/max_output_chars）。

    Raises:
        CoreError: 能力未注册（CAPABILITY_DENIED，fail-closed）。
    """
    spec = REGISTRY.get(capability_id)
    if spec is None:
        raise CoreError.business(ReasonCode.CAPABILITY_DENIED, f"unregistered capability: {capability_id!r}")
    detail: JsonObject = {
        "id": spec.id,
        "kind": spec.kind,
        "detail_ref": spec.detail_ref,
        "payload_ref": spec.payload_ref,
        "max_output_chars": spec.max_output_chars,
    }
    # FR-5 / S1：L1 返回真实 schema（tool）或规程元数据（skill），不是占位串
    if spec.args_model is not None:
        detail["args_schema"] = spec.args_model.model_json_schema()
    if spec.kind == "skill":
        detail["manifest"] = {"id": spec.id, "cost_hint": spec.cost_hint, "version_ref": spec.detail_ref}
    return detail
