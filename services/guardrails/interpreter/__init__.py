"""v3 三层解释器 public API（ADR-016）。

规则 → 本地小模型（onnxruntime，podman 卷挂载）→ 云端 LLM 兜底。
本地层不算 LLM（U-3 用户拍板）；只有 CLOUD 层才调 model.complete。
"""
from services.guardrails.interpreter.local_model import LocalModelInterpreter
from services.guardrails.interpreter.router import InterpreterRouter
from services.guardrails.interpreter.rule import RuleInterpreter
from services.guardrails.interpreter.types import (
    FallbackReason,
    InterpretResult,
    LayerUsed,
    SlotFill,
    WeightsManifest,
)


class Interpreter:
    """解释器协议（鸭子类型声明）：interpret(description, attachments, fields)。"""

    def interpret(self, description: str, attachments=(), fields=None) -> InterpretResult:
        raise NotImplementedError


__all__ = [
    "FallbackReason",
    "InterpretResult",
    "Interpreter",
    "InterpreterRouter",
    "LayerUsed",
    "LocalModelInterpreter",
    "RuleInterpreter",
    "SlotFill",
    "WeightsManifest",
]
