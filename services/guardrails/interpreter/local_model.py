"""本地小模型解释层（ADR-016 第 2 层）：onnxruntime BIO 序列标注。

权重走 podman 只读卷挂载（/infra/models/slot-tagger/<version>/），缺权重
fail-fast 告警降级，**禁止用 bigram 冒充**（实测 0.40-0.59 全不达标）。
注入 weights（带 tag(text)->dict 的对象）即可 mock 测试，不依赖真 onnx 文件。
"""
from __future__ import annotations

import logging
import math
import re

from packages.contracts.enums import GridSpec
from services.guardrails.interpreter.types import (
    FallbackReason,
    InterpretResult,
    LayerUsed,
    SlotFill,
)

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/infra/models/slot-tagger/v1"


class LocalModelInterpreter:
    """本地模型层：只填槽、不做安全裁判；输出过 schema/取值域校验。"""

    def __init__(self, *, weights=None, path: str = _DEFAULT_PATH,
                 threshold: float = 0.75) -> None:
        self._weights = weights
        self._path = path
        self.threshold = threshold

    def interpret(self, description: str, attachments=(), fields=None) -> InterpretResult:
        if self._weights is None:
            logger.warning("本地模型权重不可用（path=%s），跳过 local 层；不回退 bigram", self._path)
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.LAYER_UNAVAILABLE)
        try:
            tagged = self._weights.tag(description)
        except Exception as exc:  # noqa: BLE001 - 端点异常降级，不跳过判定
            logger.warning("本地模型推理失败（%s），跳过 local 层", exc)
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.LAYER_UNAVAILABLE)
        if not isinstance(tagged, dict) or not tagged:
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.SCHEMA_MISMATCH)

        # 越界/拒绝信号不得被吞（review B2）：模型输出 OUT_OF_SCOPE 类键即 fail-closed
        forbidden_keys = {"intent", "reason", "out_of_scope", "reject", "decision"}
        if any(k in tagged for k in forbidden_keys):
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.SCHEMA_MISMATCH)

        # 真取值域校验（不是只查键名）：grid 必须合法枚举；值必须是描述中的 token 或附件 ref
        allowed = {"grid", "data", "host", "datetime", "style"}
        valid_grids = {g.value for g in GridSpec}
        # confidence 是元字段，不是槽位——从槽位校验中剔除
        slot_items = {k: v for k, v in tagged.items() if k != "confidence"}
        slots: dict[str, object] = {}
        for k, v in slot_items.items():
            if k not in allowed or not isinstance(v, str) or not v:
                return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                       reason=FallbackReason.SCHEMA_MISMATCH)
            if k == "grid" and v not in valid_grids:
                return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                       reason=FallbackReason.SCHEMA_MISMATCH)
            # 带边界的 token 匹配（review S5）：禁止 `2x2` 命中 `12x20` 的子串
            if not re.search(r"(?<![0-9A-Za-z])" + re.escape(v) + r"(?![0-9A-Za-z])",
                             description, re.IGNORECASE):
                return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                       reason=FallbackReason.SCHEMA_MISMATCH)
            slots[k] = v
        if not slots:
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.SCHEMA_MISMATCH)

        # 读模型真实置信度；NaN/inf/bool/低置信 → fail-closed（review S6 + U-4）
        raw_conf = tagged.get("confidence")
        if not isinstance(raw_conf, (int, float)) or isinstance(raw_conf, bool) \
                or not math.isfinite(float(raw_conf)):
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.LOW_CONFIDENCE)
        confidence = float(raw_conf)
        if confidence < self.threshold:
            return InterpretResult(kind="fallback", layer_used=LayerUsed.LOCAL,
                                   reason=FallbackReason.LOW_CONFIDENCE)
        # P2 #36：与规则层同口径产出意图标签（本地层 P3 启用时语义一致）。
        from services.guardrails.interpreter.rule import _intent_category

        fill = SlotFill(
            intent_id=_intent_category(description),
            instance_id="local:0",
            slots=slots,
            evidence=("local-model@" + self._path,),
            confidence=confidence,
        )
        return InterpretResult(kind="compiled", instances=(fill,),
                               layer_used=LayerUsed.LOCAL, confidence=confidence)


__all__ = ["LocalModelInterpreter"]
