"""M05 视觉理解能力（ADR-003 / Q6：引入视觉模型、留接口、测试 mock）。

复用 M20 ModelGateway 发多模态消息（Message.image_ref），不另起 VisionPort。
未注入真实模型端点时降级为空结果 + degraded=True——登记为「待真实环境验证」，
不造假语义、不静默跳过。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from packages.agent_core.injection import ModelGateway
from packages.agent_core.types import Message, ModelTier

_VISION_PROMPT_SUFFIX = "\n只输出 JSON：{\"elements\": [字符串]}，不要解释。"


@dataclass(slots=True, frozen=True)
class VisionResult:
    """视觉理解结果。

    Attributes:
        elements: 识别出的界面/图像元素列表（不可信数据，供上游当参考）。
        degraded: 是否降级（无真实模型端点）；True 时 elements 为空。
        raw: 模型原始文本（留证，供回归与归因）。
    """

    elements: tuple[str, ...]
    degraded: bool
    raw: str = ""


@dataclass(slots=True, frozen=True)
class VisionAnswer:
    """通用视觉问答结果（ADR-014：一组视觉工具共享的返回形态）。

    text 是模型原始输出（当数据、不当指令）；json_value 是能解析成 JSON 时的
    结构化值，解析失败为 None 而 text 仍保留——不伪造、不静默吞掉。
    """

    text: str
    json_value: object | None
    degraded: bool


def ask_vision(
    image_ref: str,
    prompt: str,
    gateway: ModelGateway | None,
    *,
    suffix: str = "",
) -> VisionAnswer:
    """通用单图视觉调用：复用 ModelGateway 多模态消息，不另起 VisionPort。

    Args:
        image_ref: 图片 data URL（调用方由 source_ref 生成）。
        prompt: 业务提示（恒当数据，不解析为指令）。
        gateway: 注入的模型网关；None 表示无真实端点（降级，不造假）。
        suffix: 可选输出格式约束（如"只输出 JSON"），由各工具决定。

    Returns:
        VisionAnswer；降级时 text 空、degraded=True。
    """
    if gateway is None:
        return VisionAnswer(text="", json_value=None, degraded=True)
    message = Message(
        role="user",
        content=prompt + (suffix or ""),
        image_ref=image_ref,
    )
    out = gateway.complete([message], tier=ModelTier.STANDARD.value, stream=False)
    parsed: object | None
    try:
        parsed = json.loads(out.content)
    except (ValueError, TypeError):
        parsed = None
    return VisionAnswer(text=out.content, json_value=parsed, degraded=False)


def understand_image(
    image_ref: str,
    prompt: str,
    gateway: ModelGateway | None,
) -> VisionResult:
    """元素列表专用封装（保留旧签名，内部走 ask_vision）。"""
    answer = ask_vision(image_ref, prompt, gateway, suffix=_VISION_PROMPT_SUFFIX)
    if answer.degraded or not isinstance(answer.json_value, dict):
        raw_elements = ()
    else:
        maybe = answer.json_value.get("elements", ())
        raw_elements = (
            tuple(str(e) for e in maybe if isinstance(e, str))
            if isinstance(maybe, list)
            else ()
        )
    return VisionResult(elements=raw_elements, degraded=answer.degraded, raw=answer.text)
