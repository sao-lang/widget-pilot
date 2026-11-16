"""M03 模型直连与 usage 归一（§11.7 / §4.1 L1）。

用 httpx 直接请求 chat-completions 兼容端点；换模型只改配置。不同供应商
usage 字段归一到 Usage。RetryPolicy 只处理基础设施故障（429/5xx/网络），
指数退避有上限（§4.4/§4.5）；业务失败不重试。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from packages.agent_core.errors import CoreError
from packages.agent_core.types import Message, ModelOutput, Usage


def normalize_usage(raw: dict[str, Any] | None) -> Usage:
    """跨供应商 usage 归一：OpenAI(prompt/completion) 与 Anthropic(input/output)。"""
    raw = raw or {}
    inp = raw.get("input_tokens", raw.get("prompt_tokens", raw.get("promptTokens", 0))) or 0
    out = raw.get("output_tokens", raw.get("completion_tokens", raw.get("completionTokens", 0))) or 0
    return Usage(input_tokens=int(inp), output_tokens=int(out))


def with_retry[T](
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """基础设施故障指数退避（有上限）；业务失败立即上抛，不重试。"""
    sleeper = sleep or time.sleep
    last: CoreError | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except CoreError as e:
            if e.retryable is False:
                raise  # 业务失败不重试
            last = e
            if attempt < max_retries:
                sleeper(base_delay * (2 ** attempt))
        except Exception as e:  # noqa: BLE001 - 未知异常按基础设施故障处理（有上限）
            # 未知异常按基础设施故障处理（有上限）
            last = CoreError.infra("MODEL_ERROR", str(e))
            if attempt < max_retries:
                sleeper(base_delay * (2 ** attempt))
    raise last or CoreError.infra("MODEL_ERROR", "retry exhausted")


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
    if not endpoint:
        raise CoreError.infra("MODEL_ERROR", "model gateway endpoint not configured")

    def call() -> dict[str, Any]:
        import httpx
        resp = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [m.to_dict() for m in messages], "stream": stream},
            timeout=60.0,
        )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise CoreError.infra("MODEL_ERROR", f"HTTP {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()

    data = with_retry(call)
    content = ""
    choices = data.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content", "") or ""
    return ModelOutput(
        content=content,
        usage=normalize_usage(data.get("usage")),
        tier=tier,
        raw=data,
    )
