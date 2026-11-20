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


def understand_image(
    image_ref: str,
    prompt: str,
    gateway: ModelGateway | None,
) -> VisionResult:
    """用注入的模型网关对图片做语义理解。

    Args:
        image_ref: 图片 data URL 或对象存储引用。
        prompt: 图像理解提示（业务侧给，如"描述界面元素"）。
        gateway: ModelGateway 注入点；None 表示当前环境无真实模型（降级）。

    Returns:
        VisionResult；降级时 elements 空、degraded=True。
    """
    if gateway is None:
        return VisionResult(elements=(), degraded=True)
    message = Message(role="user", content=prompt + _VISION_PROMPT_SUFFIX, image_ref=image_ref)
    out = gateway.complete([message], tier=ModelTier.STANDARD.value, stream=False)
    try:
        payload = json.loads(out.content)
        raw_elements = payload.get("elements", []) if isinstance(payload, dict) else []
        elements = tuple(str(e) for e in raw_elements if isinstance(e, str))
    except (ValueError, TypeError):
        elements = ()
    return VisionResult(elements=elements, degraded=False, raw=out.content)
