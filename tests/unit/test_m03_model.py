"""M03 model 层：T22 RetryPolicy 429/5xx 退避有上限 / usage 归一 / SSE 分帧。"""
from __future__ import annotations

import pytest

from packages.agent_core.constants import RETRY_MAX
from packages.agent_core.errors import CoreError
from packages.agent_core.model.provider import normalize_usage, with_retry
from packages.agent_core.model.stream import StreamDecoder, parse
from packages.agent_core.types import StreamBuffer


# ---- T22：429/5xx 退避有上限，超限产出 MODEL_ERROR，不改变 repair_count ----
def test_T22_retry_exhausted_produces_model_error():
    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise CoreError.infra("MODEL_ERROR", "429 rate limited")

    with pytest.raises(CoreError) as ei:
        with_retry(always_429, max_retries=RETRY_MAX, base_delay=0)
    assert ei.value.code == "MODEL_ERROR"
    assert ei.value.retryable is True
    assert calls["n"] == RETRY_MAX + 1  # 首次 + 上限次重试


def test_retry_succeeds_after_transient_failure():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise CoreError.infra("MODEL_ERROR", "503")
        return "ok"

    assert with_retry(flaky, max_retries=3, base_delay=0) == "ok"
    assert calls["n"] == 2


def test_retry_does_not_retry_business_failure():
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        raise CoreError.business("SPEC_CONFLICT", "business")

    with pytest.raises(CoreError) as ei:
        with_retry(fail, max_retries=3, base_delay=0)
    assert ei.value.code == "SPEC_CONFLICT"
    assert calls["n"] == 1  # 业务失败不重试


# ---- usage 归一（跨供应商字段差异）----
def test_normalize_usage_openai_style():
    u = normalize_usage({"prompt_tokens": 100, "completion_tokens": 50})
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    assert u.total_tokens == 150


def test_normalize_usage_anthropic_style():
    u = normalize_usage({"input_tokens": 30, "output_tokens": 12})
    assert u.input_tokens == 30
    assert u.output_tokens == 12


# ---- SSE 分帧 + 半帧缓存 ----
def test_sse_parse_single_frame():
    buf = StreamBuffer()
    deltas = parse('data: {"delta":"你"}\n\n'.encode(), buf)
    assert len(deltas) == 1
    assert deltas[0].text == "你"


def test_sse_half_frame_buffered():
    dec = StreamDecoder()
    out1 = dec.feed(b'data: {"delta":"hel')
    assert out1 == []  # 半帧缓存，不出 delta
    out2 = dec.feed(b'lo"}\n\n')
    assert len(out2) == 1
    assert out2[0].text == "hello"


def test_sse_done_marker():
    dec = StreamDecoder()
    out = dec.feed(b'data: [DONE]\n\n')
    assert len(out) == 1
    assert out[0].done is True
