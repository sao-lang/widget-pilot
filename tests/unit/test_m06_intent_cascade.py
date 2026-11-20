"""M06 A22/A23/A24/A25：四级级联意图裁定与拒绝类零模型调用。"""

from __future__ import annotations

from services.guardrails.intent_cascade import classify_intent
from services.guardrails.semantic_router import default_embedder
from services.guardrails.types import (
    Embedding,
    IntentCategory,
    LowTierResult,
    RouteLayer,
)


class CountingEmbedder:
    """计数桩：L2 embedder，返回可控相似度。"""

    def __init__(self, similarity: float) -> None:
        self.calls = 0
        self.fixed_score = similarity

    def embed(self, text: str) -> Embedding:
        self.calls += 1
        return Embedding(values=(1.0, 0.0))

    def similarity(self, a: Embedding, b: Embedding) -> float:
        return self.fixed_score


class CountingClassifier:
    """计数桩：L3 分类器。"""

    def __init__(self, result: LowTierResult) -> None:
        self.calls = 0
        self.result = result

    def classify(self, text: str, max_tokens: int) -> LowTierResult:
        self.calls += 1
        return self.result


def test_l1_positive_widget_generation() -> None:
    """L1 正向词表命中，不触模型。"""
    emb = CountingEmbedder(0.0)
    clf = CountingClassifier(LowTierResult(None, False))
    decision = classify_intent("做一个能翻月的日历", embedder=emb, classifier=clf)
    assert decision.intent is IntentCategory.WIDGET_GENERATION
    assert decision.route_layer is RouteLayer.L1
    assert emb.calls == 0
    assert clf.calls == 0


def test_a24_reject_never_touches_models() -> None:
    """A24：越界意图 L1 拒绝，L2/L3/L4 调用全 0。"""
    emb = CountingEmbedder(0.99)
    clf = CountingClassifier(LowTierResult(IntentCategory.WIDGET_GENERATION, True))
    decision = classify_intent("今天天气怎么样", embedder=emb, classifier=clf)
    assert decision.intent is IntentCategory.OUT_OF_SCOPE
    assert decision.route_layer is RouteLayer.L1
    assert emb.calls == 0
    assert clf.calls == 0


def test_a22_l2_semantic_route_hit() -> None:
    """A22：L1 未命中，L2 相似度 ≥ 阈值 → L2，不触 L3/L4。"""
    emb = CountingEmbedder(0.86)
    clf = CountingClassifier(LowTierResult(None, False))
    decision = classify_intent(
        "给我整一个能显示日期的小玩意", embedder=emb, classifier=clf
    )
    assert decision.route_layer is RouteLayer.L2
    assert decision.similarity == 0.86
    assert decision.threshold_hit is True  # L2 阈值命中
    assert clf.calls == 0


def test_threshold_hit_false_on_l3_l4() -> None:
    """routing.threshold_hit 语义：L3/L4 是阈值未命中升级，不能误报 True。"""
    emb = CountingEmbedder(0.41)
    clf = CountingClassifier(LowTierResult(IntentCategory.WIDGET_GENERATION, True))
    d = classify_intent("给我整一个能显示日期的小玩意", embedder=emb, classifier=clf)
    assert d.route_layer is RouteLayer.L3
    assert d.threshold_hit is False  # L2 未命中才升 L3


def test_a23_l3_classifier_fallback() -> None:
    """A23：L2 低置信 → L3 分类器裁定。"""
    emb = CountingEmbedder(0.41)
    clf = CountingClassifier(
        LowTierResult(IntentCategory.WIDGET_GENERATION, True, tokens_used=100)
    )
    decision = classify_intent(
        "给我整一个能显示日期的小玩意", embedder=emb, classifier=clf
    )
    assert decision.route_layer is RouteLayer.L3
    assert clf.calls == 1


def test_default_embedder_is_zero_dependency() -> None:
    """默认 embedder 零依赖且相似度可区分不同文本（非恒 1.0 假绿）。"""
    emb = default_embedder()
    same = emb.similarity(emb.embed("做一个日历"), emb.embed("做一个日历"))
    diff = emb.similarity(emb.embed("日历"), emb.embed("时钟"))
    assert abs(same - 1.0) < 1e-9  # 浮点归一化误差
    assert diff < 0.5  # 不同文本必须可区分，否则 L2 判定失效
