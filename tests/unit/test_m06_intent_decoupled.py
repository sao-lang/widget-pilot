"""ADR-004 全级联去类型化行为测试（RED→GREEN）。

锁定三条硬样本不变，同时要求 L1/L2/语义层不再按 widget 类型裁定。
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

import services.guardrails.constants as gc
from packages.contracts.enums import GridSpec
from services.guardrails import semantic_router
from services.guardrails.change_gate import capture_snapshot, diff_config
from services.guardrails.intent_cascade import _l1_rule_intent
from services.guardrails.patterns import intent as intent_patterns
from services.guardrails.semantic import validate_semantic
from services.guardrails.types import IntentCategory

LONG_TAIL = "给我整一个能显示日期的小玩意"
TYPE_WORDS = ("日历", "时钟", "闹钟", "待办", "calendar", "clock", "alarm", "task")


@pytest.mark.parametrize(
    "description",
    [
        "桌面上显示未来一周天气",
        "帮我做个锁屏倒计时",
        "把快递进度放到主屏",
        "主屏加个智能家居面板",
        "能帮我在桌面加个天气吗？",
        "负一屏挂个股票走势卡片",
    ],
)
def test_l1_accepts_broad_widget_scenarios(description: str) -> None:
    """11 类场景中的新说法须由 L1 直接放行，无需出现类型名。"""
    assert _l1_rule_intent(description) is IntentCategory.WIDGET_GENERATION


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("今天天气怎么样", IntentCategory.OUT_OF_SCOPE),
        ("讲个笑话", IntentCategory.OUT_OF_SCOPE),
        ("写个爬虫", IntentCategory.OUT_OF_SCOPE),
        ("用 Swift 做个小组件项目", IntentCategory.OUT_OF_SCOPE),
        ("天气", IntentCategory.OUT_OF_SCOPE),
        ("小组件怎么添加", IntentCategory.OUT_OF_SCOPE),
        ("帮我做一个聊天界面控件", IntentCategory.WIDGET_GENERATION),
        ("做一个天气预报的日历 widget", IntentCategory.WIDGET_GENERATION),
        ("生成一个翻译任务清单卡片", IntentCategory.WIDGET_GENERATION),
        (LONG_TAIL, None),
    ],
)
def test_l1_hard_samples_and_out_of_scope(
    description: str, expected: IntentCategory | None
) -> None:
    """硬样本与长尾分层不因扩表而改变。"""
    assert _l1_rule_intent(description) is expected


def test_l1_positive_patterns_include_host_anchor() -> None:
    """正向触发必须含宿主/产物锚点，而非仅靠类型词。"""
    joined = " ".join(gc.WIDGET_GENERATION_PATTERNS)
    assert "桌面" in joined and "锁屏" in joined
    assert "小组件" in joined


def test_l2_samples_drop_widget_type_words() -> None:
    """L2 样本库与共享词表不得把 widget 类型当分类键。"""
    samples = " ".join(semantic_router._SAMPLES[IntentCategory.WIDGET_GENERATION])
    for word in TYPE_WORDS:
        assert word not in samples
        assert word not in semantic_router._VOCAB


def test_l2_samples_cover_common_scenarios() -> None:
    """样本库覆盖常用说法：生成意图 ≥ 50 条，澄清回答 ≥ 10 条。"""
    assert len(semantic_router._SAMPLES[IntentCategory.WIDGET_GENERATION]) >= 50
    assert len(semantic_router._SAMPLES[IntentCategory.CLARIFICATION_ANSWER]) >= 10


def test_l2_routes_novel_widget_utterance() -> None:
    """去重类型词后，新场景说法仍能被默认 embedder 路由为生成意图。"""
    router = semantic_router.SemanticRouter()
    decision = router.route("桌面上显示未来一周天气的小组件")
    assert decision.intent is IntentCategory.WIDGET_GENERATION
    assert decision.threshold_hit is True


def test_semantic_slot_accepts_anchor_without_type_word() -> None:
    """语义层 R1：宿主锚点 + 数据词即可满足槽位，不再要求类型词。"""
    result = validate_semantic("主屏放个股票走势组件", [], GridSpec._4X4)
    assert result.ok is True


def test_semantic_hint_no_longer_lists_widget_types() -> None:
    """描述不足时的提示不得再举例"日历还是时钟"。"""
    result = validate_semantic("随便做一个东西", [], None)
    assert result.ok is False
    assert result.suggestions
    assert all("日历" not in s and "时钟" not in s for s in result.suggestions)


def test_density_hints_replace_widget_type_table() -> None:
    """密度建议以内容特征为键，旧 WidgetType 表不再作为事实来源。"""
    assert hasattr(gc, "DENSITY_HINTS")
    assert not hasattr(gc, "WIDGET_TYPICAL_GRIDS")


def test_intent_pattern_rule_is_derived_from_constants() -> None:
    """进护栏的负向 regex 必须与 L1 负向表同源。"""
    expected = "(?:" + "|".join(gc.INTENT_GUARD_PATTERNS) + ")"
    assert intent_patterns.RULES[0].regex == expected
    assert re.search(expected, "讲个笑话")
    # 疑问句合法控件请求不得被进护栏二次误拒（复审 B1）
    assert not re.search(expected, "能帮我在桌面加个天气吗？")


@pytest.mark.parametrize(
    "description",
    [
        "天气怎么样啊，我想在桌面加一个",
        "今天天气怎么样？顺便在桌面加个天气卡片",
        "金价多少？在主屏摆个金价组件",
    ],
)
def test_intent_guard_ignores_word_order(description: str) -> None:
    """疑问词在宿主之前时，进护栏也不得把合法控件请求误拒（复审 Round 2 S1）。"""
    expected = "(?:" + "|".join(gc.INTENT_GUARD_PATTERNS) + ")"
    assert not re.search(expected, description)
    assert _l1_rule_intent(description) is IntentCategory.WIDGET_GENERATION


def test_change_gate_tracks_intent_vocabulary() -> None:
    """词表/样本版本进入配置指纹。"""
    assert hasattr(gc, "INTENT_VOCAB_VERSION")
    snapshot = capture_snapshot()
    assert snapshot.intent_vocab_version == (
        f"{gc.INTENT_VOCAB_VERSION}:{gc.INTENT_SAMPLES_VERSION}"
    )
    changed = capture_snapshot()
    # 构造只有词表版本不同的基线，验证指纹可触发回归门禁
    assert diff_config(replace(changed, intent_vocab_version="v0:v2"), changed) == [
        "intent_vocab_version"
    ]
