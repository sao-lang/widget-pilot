"""M06 四级路由接线点测试（全 mock，零真实模型 / 零真实端点）。

验证本模块已做的部分：级联判定、升级判据、降级归因、默认兜底、
注入端口契约（ports.py 三个 Protocol）。M20/M07 的真实实现后续按同一 Protocol 注入。
"""

from __future__ import annotations

import pytest

from services.guardrails.intent_cascade import classify_intent
from services.guardrails.ports import Embedder, IntentClassifier, IntentGateway
from services.guardrails.types import (
    Embedding,
    IntentCategory,
    LowTierResult,
    RouteLayer,
)

# ── mock 桩（计数 + 可脚本化行为）───────────────────────────────────────────


class FakeEmbedder:
    """L2 mock：embed / similarity 计数，相似度可脚本化，可模拟端点抛错。"""

    def __init__(self, similarity: float = 0.9, fail: bool = False) -> None:
        self.calls = 0
        self.similarity_score = similarity
        self.fail = fail

    def embed(self, text: str) -> Embedding:
        self.calls += 1
        if self.fail:
            raise RuntimeError("mock embedder 端点不可用")
        return Embedding(values=(1.0,))

    def similarity(self, a: Embedding, b: Embedding) -> float:
        return self.similarity_score


class FakeClassifier:
    """L3 mock：classify 计数，返回可脚本化，可模拟端点抛错。"""

    def __init__(self, result: LowTierResult | None = None, fail: bool = False) -> None:
        self.calls = 0
        self.result = result or LowTierResult(None, False)
        self.fail = fail

    def classify(self, text: str, max_tokens: int) -> LowTierResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("mock classifier 端点不可用")
        return self.result


class FakeGateway:
    """L4 mock：resolve 计数，返回可脚本化，可模拟端点抛错。"""

    def __init__(self, result: LowTierResult | None = None, fail: bool = False) -> None:
        self.calls = 0
        self.result = result or LowTierResult(IntentCategory.WIDGET_GENERATION, True)
        self.fail = fail

    def resolve(self, text: str, max_tokens: int) -> LowTierResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("mock gateway 端点不可用")
        return self.result


LONG_TAIL = "给我整一个能显示日期的小玩意"  # L1 词表未命中


# ── 接线点契约：默认实现满足 Protocol ──────────────────────────────────────


def test_defaults_satisfy_protocols() -> None:
    """三个默认零依赖实现都满足注入契约（runtime_checkable Protocol）。"""
    from services.guardrails.intent_classifier import (
        RuleBasedClassifier,
        StructuredFallback,
    )
    from services.guardrails.semantic_router import DefaultEmbedder

    assert isinstance(DefaultEmbedder(), Embedder)
    assert isinstance(RuleBasedClassifier(), IntentClassifier)
    assert isinstance(StructuredFallback(), IntentGateway)


def test_mocks_satisfy_protocols() -> None:
    """测试桩与真实注入实现同契约——后续 M20/M07 照此实现即可对接。"""
    assert isinstance(FakeEmbedder(), Embedder)
    assert isinstance(FakeClassifier(), IntentClassifier)
    assert isinstance(FakeGateway(), IntentGateway)


# ── L1 拒绝类：零模型调用 ───────────────────────────────────────────────────


def test_l1_reject_never_calls_any_port() -> None:
    """I14：越界在 L1 拦截，L2/L3/L4 三个 mock 调用次数全 0。"""
    emb, clf, gw = FakeEmbedder(0.99), FakeClassifier(), FakeGateway()
    d = classify_intent("今天天气怎么样", embedder=emb, classifier=clf, gateway=gw)
    assert d.intent is IntentCategory.OUT_OF_SCOPE
    assert d.route_layer is RouteLayer.L1
    assert (emb.calls, clf.calls, gw.calls) == (0, 0, 0)


# ── L2 命中：不触 L3/L4 ────────────────────────────────────────────────────


def test_l2_hit_does_not_call_l3_l4() -> None:
    """A22：L2 mock 相似度 0.86 ≥ 阈值 → 裁定于 L2，L3/L4 调用 0。"""
    emb, clf, gw = FakeEmbedder(0.86), FakeClassifier(), FakeGateway()
    d = classify_intent(LONG_TAIL, embedder=emb, classifier=clf, gateway=gw)
    assert d.route_layer is RouteLayer.L2
    assert d.threshold_hit is True
    assert (clf.calls, gw.calls) == (0, 0)


# ── L3 命中：不触 L4 ───────────────────────────────────────────────────────


def test_l3_hit_does_not_call_l4() -> None:
    """A23：L2 低置信（0.41）→ L3 mock 可解析裁定，L4 调用 0。"""
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(LowTierResult(IntentCategory.WIDGET_GENERATION, True, 100))
    gw = FakeGateway()
    d = classify_intent(LONG_TAIL, embedder=emb, classifier=clf, gateway=gw)
    assert d.route_layer is RouteLayer.L3
    assert d.model_tier is not None and d.model_tier.value == "cheap"
    assert gw.calls == 0


# ── L4 注入命中（本轮补齐的接线点）──────────────────────────────────────────


def test_l4_gateway_injected_and_called() -> None:
    """L4 接线点：L2/L3 均未裁定 → 调用注入的 Gateway mock 裁定。"""
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(LowTierResult(None, False))  # 不可解析 → 升 L4
    gw = FakeGateway(LowTierResult(IntentCategory.WIDGET_GENERATION, True, 30))
    d = classify_intent(LONG_TAIL, embedder=emb, classifier=clf, gateway=gw)
    assert d.route_layer is RouteLayer.L4
    assert gw.calls == 1
    assert d.model_tier is not None and d.model_tier.value == "frontier"


def test_l2_out_of_scope_is_not_used_as_reject() -> None:
    """安全红线：L2 样本库含 OUT_OF_SCOPE 时命中也不采纳——拒绝类只能出自 L1。"""
    emb = FakeEmbedder(0.95)
    # 直接让 route 返回 OUT_OF_SCOPE（模拟真实样本库覆盖封闭意图集）
    import services.guardrails.intent_cascade as ic
    from services.guardrails.types import RouteResult

    original = ic._try_route
    ic._try_route = lambda *_a, **_k: (  # type: ignore[assignment]
        RouteResult(IntentCategory.OUT_OF_SCOPE, 0.95, True),
        None,
    )
    try:
        clf = FakeClassifier(LowTierResult(IntentCategory.WIDGET_GENERATION, True))
        d = classify_intent(
            LONG_TAIL, embedder=emb, classifier=clf, gateway=FakeGateway()
        )
        assert d.intent is not IntentCategory.OUT_OF_SCOPE
        assert d.route_layer in {RouteLayer.L3, RouteLayer.L4}
    finally:
        ic._try_route = original  # type: ignore[assignment]


def test_l4_gateway_reject_is_not_used_as_security_judge() -> None:
    """安全红线：L4 即使返回 OUT_OF_SCOPE 也不采纳——拒绝类只能出自 L1。"""
    alerts: list[str] = []
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(LowTierResult(None, False))
    gw = FakeGateway(LowTierResult(IntentCategory.OUT_OF_SCOPE, True))
    d = classify_intent(
        LONG_TAIL, embedder=emb, classifier=clf, gateway=gw, on_fallback=alerts.append
    )
    assert d.route_layer is RouteLayer.L4
    assert d.intent is IntentCategory.WIDGET_GENERATION  # 回落到放行方向兜底
    # L4 违规输出必须留证告警（Should 修复）
    assert d.fallback_reason == "L4_OUTPUT_OUT_OF_SCOPE"
    assert "L4_OUTPUT_OUT_OF_SCOPE" in alerts


# ── 降级编排（端点不可用 / 不可解析）───────────────────────────────────────


def test_l2_unavailable_degrades_to_l3_with_reason() -> None:
    """L2 embedder 抛错 → 降级 L3，fallback_reason 留归因，告警回调收到。"""
    alerts: list[str] = []
    emb = FakeEmbedder(fail=True)
    clf = FakeClassifier(LowTierResult(IntentCategory.WIDGET_GENERATION, True))
    d = classify_intent(
        LONG_TAIL,
        embedder=emb,
        classifier=clf,
        on_fallback=alerts.append,
    )
    assert d.route_layer is RouteLayer.L3
    assert d.fallback_reason == "L2_EMBEDDER_UNAVAILABLE"
    assert "L2_EMBEDDER_UNAVAILABLE" in alerts


def test_l3_unavailable_degrades_to_l4_with_reason() -> None:
    """L3 classifier 抛错 → 升 L4，fallback_reason 留归因。"""
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(fail=True)
    gw = FakeGateway()
    d = classify_intent(
        LONG_TAIL,
        embedder=emb,
        classifier=clf,
        gateway=gw,
    )
    assert d.route_layer is RouteLayer.L4
    assert d.fallback_reason == "L3_CLASSIFIER_UNAVAILABLE"
    assert gw.calls == 1


def test_l3_unparseable_degrades_to_l4_with_reason() -> None:
    """L3 输出不可解析 → 升 L4，fallback_reason=L3_OUTPUT_UNPARSEABLE。"""
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(LowTierResult(IntentCategory.WIDGET_GENERATION, False))
    gw = FakeGateway()
    d = classify_intent(LONG_TAIL, embedder=emb, classifier=clf, gateway=gw)
    assert d.route_layer is RouteLayer.L4
    assert d.fallback_reason == "L3_OUTPUT_UNPARSEABLE"


def test_l4_unavailable_falls_back_to_structured() -> None:
    """L4 gateway 抛错 → 回落结构化兜底，不跳过判定放行，归因 L4。"""
    alerts: list[str] = []
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(LowTierResult(None, False))
    gw = FakeGateway(fail=True)
    d = classify_intent(
        LONG_TAIL,
        embedder=emb,
        classifier=clf,
        gateway=gw,
        on_fallback=alerts.append,
    )
    assert d.route_layer is RouteLayer.L4
    assert d.intent is IntentCategory.WIDGET_GENERATION
    assert d.fallback_reason == "L4_GATEWAY_UNAVAILABLE"
    assert "L4_GATEWAY_UNAVAILABLE" in alerts


# ── 默认实现：未注入任何端口也能裁定 ────────────────────────────────────────


def test_no_ports_injected_still_resolves() -> None:
    """未注入任何真实端口 → 用默认零依赖实现，仍能裁定，不静默跳过。"""
    d = classify_intent(LONG_TAIL)
    assert d.resolved is True
    assert d.route_layer in {RouteLayer.L2, RouteLayer.L3, RouteLayer.L4}
    assert d.intent in {
        IntentCategory.WIDGET_GENERATION,
        IntentCategory.CLARIFICATION_ANSWER,
    }


@pytest.mark.parametrize(
    "reason_fragment",
    ["UNAVAILABLE", "UNPARSEABLE"],
)
def test_fallback_reason_is_attributable(reason_fragment: str) -> None:
    """降级归因字符串稳定可查（供 M11 告警 / I15 复算）。"""
    emb = FakeEmbedder(0.41)
    clf = FakeClassifier(fail=(reason_fragment == "UNAVAILABLE"))
    if reason_fragment == "UNPARSEABLE":
        clf = FakeClassifier(LowTierResult(None, False))
    d = classify_intent(LONG_TAIL, embedder=emb, classifier=clf, gateway=FakeGateway())
    assert d.fallback_reason is not None
    assert reason_fragment in d.fallback_reason
