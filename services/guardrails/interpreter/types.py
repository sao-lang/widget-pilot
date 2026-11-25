"""v3 解释器共享类型（ADR-016）：SlotFill / InterpretResult / 分层枚举。

纯类型定义，无模型依赖；所有实现模块只依赖本文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class LayerUsed(StrEnum):
    """解释发生在哪一层；local 不算云端 LLM（U-3 用户拍板）。"""

    RULE = "rule"
    LOCAL = "local"
    CLOUD = "cloud"


class FallbackReason(StrEnum):
    """降级/回落归因。"""

    LAYER_UNAVAILABLE = "LAYER_UNAVAILABLE"  # 本地权重缺失/损坏
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"  # 模型输出不合 schema
    LOW_CONFIDENCE = "LOW_CONFIDENCE"  # 低于阈值
    STUB_FAIL = "STUB_FAIL"  # mock/测试用


@dataclass(slots=True, frozen=True)
class SlotFill:
    """单个 widget 实例的结构化槽位（一次编译/解释的产物）。"""

    intent_id: str
    instance_id: str
    slots: dict[str, object] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0


@dataclass(slots=True, frozen=True)
class InterpretResult:
    """解释器统一输出；kind 决定下游分流。"""

    kind: str  # compiled | clarification | fallback
    instances: tuple[SlotFill, ...] = ()
    layer_used: LayerUsed = LayerUsed.RULE
    missing_slots: tuple[str, ...] = ()
    question_kind: str = ""
    reason: FallbackReason | str = ""
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()

    @classmethod
    def fallback(cls, reason: FallbackReason | str) -> InterpretResult:
        return cls(kind="fallback", reason=reason)


@dataclass(slots=True, frozen=True)
class WeightsManifest:
    """本地模型权重清单（podman 只读卷挂载下的诚信校验）。"""

    version: str
    sha256: str
    sample_count: int
    intent_families: tuple[str, ...] = ()
    license: str = ""
