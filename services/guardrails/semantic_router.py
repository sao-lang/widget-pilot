"""L2 语义路由（§4.3）：本地 embedding + 意图样本库余弦相似度 + 版本化阈值。

默认 Embedder 为纯代码字符 bigram + 关键词权重实现（零依赖、可离线跑）；
真实 embedding 模型由部署环境注入实现（见 m06-docker-and-model-testing.md）。
"""

from __future__ import annotations

import math
import re

from services.guardrails.constants import (
    INTENT_SAMPLES_VERSION,
    SEMANTIC_ROUTE_THRESHOLD,
)
from services.guardrails.ports import Embedder
from services.guardrails.types import Embedding, IntentCategory, RouteResult

# 意图样本库（封闭集合，v1 内置最小样本；真实样本库版本化注入时替换）
_SAMPLES: dict[IntentCategory, tuple[str, ...]] = {
    IntentCategory.WIDGET_GENERATION: (
        "生成手机控件 日历 时钟 待办",
        "做一个能翻月的日历控件",
        "给我一个时钟组件",
    ),
    IntentCategory.CLARIFICATION_ANSWER: (
        "选择四乘四",
        "用 4x4 规格",
        "就这个规格",
    ),
}


def _normalize(text: str) -> str:
    """归一化文本：小写、去空白。"""
    return re.sub(r"\s+", "", text.lower())


# 共享词表（关键修正）：所有文本映射到同一 bigram 词表，向量才可比。
# 用样本库 + 常见控件词构造，避免「不同词表按位置对齐」导致的恒 1.0 假相似。
_VOCAB: tuple[str, ...] = tuple(
    sorted(
        {
            _normalize(text)[i : i + 2]
            for samples in _SAMPLES.values()
            for text in samples
            for i in range(len(_normalize(text)) - 1)
        }
        | {"日历", "时钟", "闹钟", "待办", "控件", "翻月", "日期", "组件"}
    )
)
_VOCAB_INDEX: dict[str, int] = {token: idx for idx, token in enumerate(_VOCAB)}


class DefaultEmbedder:
    """纯代码向量化：共享 bigram 词表 + L2 归一化（零依赖、可离线判定）。"""

    def embed(self, text: str) -> Embedding:
        """把文本映射到共享词表上的单位向量。"""
        normalized = _normalize(text)
        vec = [0.0] * len(_VOCAB)
        for i in range(len(normalized) - 1):
            idx = _VOCAB_INDEX.get(normalized[i : i + 2])
            if idx is not None:
                vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return Embedding(values=tuple(v / norm for v in vec))

    def similarity(self, a: Embedding, b: Embedding) -> float:
        """共享词表上的余弦相似度（向量等长、逐维对齐）。"""
        n = min(len(a.values), len(b.values))
        if n == 0:
            return 0.0
        return sum(a.values[i] * b.values[i] for i in range(n))


def default_embedder() -> DefaultEmbedder:
    """构造默认零依赖 embedder（真实模型由部署注入替换）。"""
    return DefaultEmbedder()


class SemanticRouter:
    """L2 语义路由器（§3.1）。

    Attributes:
        embedder: 向量化端口（默认纯代码，可注入真实模型）。
        threshold: 版本化相似度阈值，低于则升级 L3。
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        threshold: float = SEMANTIC_ROUTE_THRESHOLD,
    ) -> None:
        self.embedder = embedder or DefaultEmbedder()
        self.threshold = threshold
        self._sample_vectors = {
            intent: tuple(self.embedder.embed(s) for s in samples)
            for intent, samples in _SAMPLES.items()
        }

    @property
    def samples_version(self) -> str:
        """意图样本库版本（留证用）。"""
        return INTENT_SAMPLES_VERSION

    def route(self, text: str) -> RouteResult:
        """对文本算最高相似度意图；达阈值则裁定，否则低置信交 L3。"""
        query = self.embedder.embed(text)
        best_intent: IntentCategory | None = None
        best_score = 0.0
        for intent, vectors in self._sample_vectors.items():
            for vector in vectors:
                score = self.embedder.similarity(query, vector)
                if score > best_score:
                    best_score = score
                    best_intent = intent
        hit = best_score >= self.threshold
        return RouteResult(
            intent=best_intent if hit else None,
            similarity=best_score,
            threshold_hit=hit,
        )
