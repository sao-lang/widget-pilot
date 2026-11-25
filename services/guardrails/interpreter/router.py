"""三层解释器路由（ADR-016）：规则 → 本地小模型 → 云端兜底。

严格单向降级，禁止「云端失败再回编译」；本地层不算 LLM（U-3）。
"""
from __future__ import annotations

import logging

from services.guardrails.interpreter.types import InterpretResult, LayerUsed

logger = logging.getLogger(__name__)


class InterpreterRouter:
    """按层调用解释器；rule=None 表示规则层不可用（测试/降级场景）。"""

    def __init__(self, *, rule=None, local_model=None, threshold: float = 0.8) -> None:
        self._rule = rule
        self._local = local_model
        self.threshold = threshold

    def interpret(self, description: str, attachments=(), fields=None) -> InterpretResult:
        # 第 1 层：规则（零模型）
        if self._rule is not None:
            result = self._rule.interpret(description, attachments, fields)
            if result.kind == "compiled" and result.confidence >= self.threshold:
                return result
            if result.kind == "clarification":
                # 可补槽位：追问优先，不浪费模型调用
                return result

        # 第 2 层：本地小模型（不出本机，不算 LLM）
        if self._local is not None:
            result = self._local.interpret(description, attachments, fields)
            if result.kind == "compiled":
                return result

        # 第 3 层：云端兜底（由调用方接 run()→CALL_MODEL；这里只标注）
        logger.info("解释器三层均未命中，落云端兜底（layer_used=cloud）")
        return InterpretResult(kind="fallback", layer_used=LayerUsed.CLOUD,
                               reason="ALL_LAYERS_FAILED")


__all__ = ["InterpreterRouter"]
