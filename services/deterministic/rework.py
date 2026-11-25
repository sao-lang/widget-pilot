"""ADR-013 §3.6 返工③：有界 LLM 返工的零模型调度层。

本模块**不调模型**：只负责取失败证据、校验端口存在、调用一次 ReworkLLMPort、
把输出收窄为合法 WidgetSpec。真实 gateway.complete 在
`services/orchestrator/rework/model_ports.py:GatewayReworkLLM`（独立接线点）。
mock 测试只证明调度不变量，不代表模型能力已验收。
"""
from __future__ import annotations

import logging
from typing import cast

from packages.contracts.types import JsonObject
from services.deterministic.compiler import assert_valid_spec
from services.orchestrator.rework.model_ports import ReworkLLMPort

logger = logging.getLogger(__name__)


def merge_rework_slots(base_spec: JsonObject, fill):
    """重成包 slot 继承：用户新值优先，未说的从目标 spec 文本反推（零模型）。

    base_spec 不显式存 data/host，只能从 intent + layout_slots[].content 反推；
    这是文本启发式而非模型推断，无法覆盖时留空由 compile_spec 用默认值。
    """
    from dataclasses import replace

    from services.guardrails.interpreter.rule import _anchors, _datas

    slots = dict(fill.slots)
    layout = base_spec.get("layout_slots")
    base_text = str(base_spec.get("intent", ""))
    if isinstance(layout, list) and layout and isinstance(layout[0], dict):
        base_text += " " + str(layout[0].get("content", ""))
    if not slots.get("data"):
        datas = _datas(base_text)
        if datas:
            slots["data"] = datas[0][1]
    if not slots.get("host"):
        anchors = _anchors(base_text)
        if anchors:
            slots["host"] = anchors[0]
    if not slots.get("grid") and base_spec.get("grid"):
        slots["grid"] = base_spec["grid"]
    return replace(fill, slots=slots)


def failure_summary(validation: object) -> tuple[str, ...]:
    """从 ValidateResult 抽确定性失败证据（不可信数据仅取文本字段）。"""
    out: list[str] = []
    reason = getattr(validation, "reason_code", None)
    if reason is not None:
        out.append(str(getattr(reason, "value", reason)))
    for attr in ("output_guard", "contract_check"):
        report = getattr(validation, attr, None)
        if report is None:
            continue
        for violation in getattr(report, "violations", ()) or ():
            text = getattr(violation, "rule_id", None) or str(violation)
            out.append(f"{attr}:{text}")
    sandbox = getattr(validation, "sandbox_test", None)
    if sandbox is not None and not bool(getattr(sandbox, "passed", False)):
        out.append(f"sandbox:{getattr(sandbox, 'reason_code', '') or 'FAILED'}")
    return tuple(out)


def try_bound_rework(
    spec: JsonObject,
    validation: object,
    description: str,
    port: ReworkLLMPort | None,
    *,
    rework_llm: str,
) -> JsonObject | None:
    """最多一次返工：无端口/off 一律 None（调用方走 FAILED，零模型）。

    - ``rework_llm != "on"`` → 不调，返回 None（off 默认零 model.complete）。
    - ``port is None`` → fail-closed 返回 None（on 但无端口不悄悄降级）。
    - 端口输出过不了 WidgetSpec schema → 视为不可信，返回 None。
    """
    if rework_llm != "on":
        return None
    if port is None:
        logger.warning("rework_llm=on but no ReworkLLMPort injected; fail-closed to FAILED")
        return None
    try:
        candidate = port.rework(spec, failure_summary(validation), description)
        assert_valid_spec(cast(JsonObject, candidate))
    except Exception as exc:  # noqa: BLE001 - 模型输出不可信，非法即拒绝
        logger.warning("bound rework rejected: %s", exc)
        return None
    return cast(JsonObject, candidate)
