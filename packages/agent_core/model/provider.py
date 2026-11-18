"""M03 模型直连与 usage 归一（§11.7 / §4.1 L1）。

用 httpx 直接请求 chat-completions 兼容端点；换模型只改配置。不同供应商
usage 字段归一到 Usage。RetryPolicy 只处理基础设施故障（429/5xx/网络），
指数退避有上限（§4.4/§4.5）；业务失败不重试。

类型改造后：provider 原始响应用 :data:`JsonObject` 承载（结构由供应商决定，
统一聚到一处），不再裸 ``dict[str, Any]``。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import cast

from packages.agent_core.errors import CoreError
from packages.agent_core.types import JsonObject, JsonValue, Message, ModelOutput, Usage
from packages.contracts.enums import ModelTier, ReasonCode


def normalize_usage(raw: JsonObject | None) -> Usage:
    """跨供应商 usage 归一：OpenAI(prompt/completion) 与 Anthropic(input/output)。

    供应商键名不固定，但值都是 JSON 数；从 JsonObject 中按优先级取整数。
    """
    data: JsonObject = raw or {}
    inp = data.get("input_tokens", data.get("prompt_tokens", data.get("promptTokens", 0))) or 0
    out = data.get("output_tokens", data.get("completion_tokens", data.get("completionTokens", 0))) or 0
    return Usage(input_tokens=int(cast(int | str | float, inp)), output_tokens=int(cast(int | str | float, out)))


def with_retry[T](
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """基础设施故障指数退避（有上限）；业务失败立即上抛，不重试。"""
    sleeper = sleep or time.sleep
    last: CoreError | None = None
    # 总尝试次数 = 首次 + max_retries 次重试，超次数即退出，避免无限重试
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except CoreError as e:
            # 业务失败重试无意义，立即上抛交由调用方走 REPAIR/终态
            if e.retryable is False:
                raise  # 业务失败不重试
            last = e
            # 仅在还有重试余量时退避，末次失败不再空等直接退出
            if attempt < max_retries:
                sleeper(base_delay * (2 ** attempt))
        except Exception as e:  # noqa: BLE001 - 未知异常按基础设施故障处理（有上限）
            # 未知异常同样受重试上限约束，耗尽后统一抛出
            last = CoreError.infra(ReasonCode.MODEL_ERROR, str(e))
            # 末次不再退避：无后续尝试时休眠只是无谓等待
            if attempt < max_retries:
                sleeper(base_delay * (2 ** attempt))
    raise last or CoreError.infra(ReasonCode.MODEL_ERROR, "retry exhausted")


def complete(
    messages: list[Message],
    tier: str = "standard",
    stream: bool = True,
    *,
    endpoint: str = "",
    api_key: str = "",
    model: str = "",
) -> ModelOutput:
    """§11.3 调用形态：httpx 直连 chat-completions 兼容端点。

    生产由服务层注入 endpoint/model；本函数保留直连实现，单测用 FakeModel
    替代（§8.1），不发起真实 HTTP。
    """

    def call() -> JsonObject:
        """发起一次 HTTP 请求并把响应体解析为 JsonObject。"""
        import httpx
        resp = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [m.to_dict() for m in messages], "stream": stream},
            timeout=60.0,
        )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise CoreError.infra(ReasonCode.MODEL_ERROR, f"HTTP {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        # httpx.Response.json() 返回 Any；此处收口为 JsonObject 单一不确定结构
        return cast(JsonObject, resp.json())

    data = with_retry(call)
    content = ""
    choices = data.get("choices") or []
    # 只取第一个候选：chat-completions 兼容端点默认单候选，多候选由供应商配置
    if isinstance(choices, list) and choices:
        first = choices[0]
        # 供应商响应字段形态不固定，逐层判型后再取，避免非 dict 触发 AttributeError
        if isinstance(first, dict):
            message = first.get("message")
            # message 可能缺失或为其他类型，判型后才读取 content
            if isinstance(message, dict):
                raw_content: JsonValue = message.get("content", "")
                content = raw_content if isinstance(raw_content, str) else ""
    # 先取 usage 再判型：isinstance 收窄作用于局部变量，避免对 get() 二次调用失效
    raw_usage = data.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else None
    return ModelOutput(
        content=content,
        usage=normalize_usage(usage),
        tier=cast(ModelTier, tier),  # tier 由服务层配置为契约档位字符串
        raw=data,
    )
