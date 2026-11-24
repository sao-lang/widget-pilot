"""M09 领域值对象（§6.2 协议 / §6.1 实体，编码规则 4：明确字段静态声明）。

所有对象 frozen + slots，跨层传递只做属性访问，明确字段静态声明，禁止动态键拼装。
BundleState / GridSpec / Capability / ModelTier 直接复用 packages.contracts
权威枚举（单一事实来源，M02）。
"""
from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.enums import BundleState, Capability, GridSpec
from packages.contracts.types import JsonObject

__all__ = [
    "BridgePolicy",
    "BundleState",
    "CardRuntime",
    "EvidenceBundle",
    "IframeRuntime",
    "RuntimeLimits",
    "VersionNode",
    "WidgetRuntime",
]


@dataclass(slots=True, frozen=True)
class VersionNode:
    """git 式版本链节点（§6.1 BundleVersion / A15）。"""

    version_id: str
    """版本节点标识（ULID/hex）。"""
    bundle_id: str
    """所属包标识。"""
    parent_id: str | None
    """父版本；首节点为 None。"""
    compiled_ref: str
    """该版编译产物对象引用（回滚沿用）。"""
    compiled_hash: str
    """该版编译产物 sha256。"""
    manifest_hash: str
    """该版 manifest 摘要。"""
    created_at: str
    """写入时刻（UTC ISO）。"""


@dataclass(slots=True, frozen=True)
class CardRuntime:
    """卡片真实比例（§6.3：cell=80px，六种规格）。"""

    grid: GridSpec
    """控件规格。"""
    w: int
    """像素宽。"""
    h: int
    """像素高。"""


@dataclass(slots=True, frozen=True)
class IframeRuntime:
    """受限 iframe 协议（§4.3.3 / A7）。"""

    sandbox: tuple[str, ...]
    """sandbox token 集合，恰 ('allow-scripts',)。"""
    csp: str
    """严格 CSP 字符串。"""
    srcdoc_ref: str
    """编译产物对象引用。"""


@dataclass(slots=True, frozen=True)
class RuntimeLimits:
    """运行时上限（§6.3：render_timeout_ms / max_message_rate）。"""

    render_timeout_ms: int
    """iframe 渲染超时熔断（默认 5000）。"""
    max_message_rate: int
    """bridge 消息速率上限（默认 60/s）。"""


@dataclass(slots=True, frozen=True)
class BridgePolicy:
    """窄 bridge 放行策略（§4.3.3 / I5/I8）。"""

    messages: tuple[str, ...]
    """7 条固定消息集合。"""
    capability_bridge: tuple[Capability, ...]
    """manifest.capabilities ∩ 全局白名单。"""
    storage_namespace: str
    """本地存储隔离命名空间（user + widget）。"""


@dataclass(slots=True, frozen=True)
class WidgetRuntime:
    """渲染协议完整下发体（§6.2 WidgetRuntime / R5）。"""

    card: CardRuntime
    """卡片规格与像素。"""
    iframe: IframeRuntime
    """受限 iframe 参数。"""
    bridge: BridgePolicy
    """bridge 放行策略。"""
    limits: RuntimeLimits
    """运行时上限。"""

    def to_json(self) -> JsonObject:
        """收窄为可序列化 JSON（供 REST preview / artifact 下发）。"""
        return {
            "card": {
                "grid": self.card.grid.value,
                "px": {"w": self.card.w, "h": self.card.h},
            },
            "iframe": {
                "sandbox": list(self.iframe.sandbox),
                "csp": self.iframe.csp,
                "srcdoc_ref": self.iframe.srcdoc_ref,
            },
            "bridge_messages": list(self.bridge.messages),
            "capability_bridge": [c.value for c in self.bridge.capability_bridge],
            "storage_namespace": self.bridge.storage_namespace,
            "limits": {
                "render_timeout_ms": self.limits.render_timeout_ms,
                "max_message_rate": self.limits.max_message_rate,
            },
        }


@dataclass(slots=True, frozen=True)
class EvidenceBundle:
    """成包输入证据（M08 产出折叠，不改结论 / I14）。"""

    static_check: JsonObject
    """{ passed, violations[] }：output_guard + contract_check 折叠。"""
    sandbox_test: JsonObject
    """{ passed, cases[], log_ref, snapshot_ref }。"""
    compiled_ref: str
    """编译产物对象 key（不含 hash 后缀）。"""
    compiled_hash: str
    """编译产物 sha256。"""
    spec_hash: str
    """sha256(canonical_json(WidgetSpec))。"""
    evidence_ref: str
    """M08 汇总证据引用（供 trace/evidence 聚合）。"""
