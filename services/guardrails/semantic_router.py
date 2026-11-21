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

# 意图样本库（v2.1 去类型化：只描述"要一个 widget"，不描述是哪一种）。
# 按 ADR-004 §9 的 11 类场景组织，样本只含宿主/动作/数据词，不含 widget 类型词
# （类型词会进 _VOCAB 并破坏去类型化约束，见 test_l2_samples_drop_widget_type_words）。
_SAMPLES: dict[IntentCategory, tuple[str, ...]] = {
    IntentCategory.WIDGET_GENERATION: (
        # 通用生成/放置：动作 × 宿主/产物锚点
        "生成一个桌面小组件",
        "帮我做个桌面挂件",
        "在主屏放个小组件",
        "锁屏加个组件",
        "负一屏摆个卡片",
        "万能卡片上放个面板",
        "服务卡片加个组件",
        "桌面智能叠放里加个组件",
        "整一个主屏小工具",
        "给我弄个桌面 widget",
        "桌面上添加一个卡片",
        "主屏新建一个小组件",
        # 信息展示
        "桌面上显示未来一周天气的小组件",
        "主屏放个能看到实时新闻的卡片",
        "桌面摆个展示空气质量的挂件",
        "小组件显示今日新闻摘要",
        "帮我做个展示气温变化的桌面卡片",
        "锁屏显示未来几天的降雨概率",
        "桌面加个空气质量指数面板",
        # 时间日程（用 日期/倒计时/日程 等说法，不写类型词）
        "帮我做个锁屏倒计时",
        "桌面放个显示日期和星期的组件",
        "主屏摆个能看倒计时的卡片",
        "做个桌面组件显示今日日程",
        "在桌面加个节日倒计时挂件",
        "帮我做个能显示秒表的小组件",
        "主屏加个显示时分秒的组件",
        "锁屏上显示今晚的倒计时",
        # 效率工具
        "桌面摆个购物清单小组件",
        "主屏放个能勾选的清单卡片",
        "帮我做个桌面备忘组件",
        "小组件显示今天要完成的事项",
        "锁屏加个显示备忘的卡片",
        "整一个能记录喝水打卡的桌面组件",
        "桌面放个便签小组件",
        # 健康运动
        "锁屏显示心率和步数",
        "桌面放个显示步数的小组件",
        "主屏加个睡眠时长卡片",
        "帮我做个能看卡路里消耗的挂件",
        "小组件展示今日运动数据",
        "桌面摆个体重记录面板",
        # 设备控制
        "主屏加个智能家居面板",
        "桌面放个控制灯光的卡片",
        "帮我做个能开关空调的小组件",
        "锁屏加个智能家居快捷面板",
        "桌面摆个控制窗帘的挂件",
        # 媒体娱乐
        "桌面放个音乐播放控制卡片",
        "主屏摆个能看正在播放歌曲的组件",
        "帮我做个音乐播放器小组件",
        "锁屏加个显示歌词的卡片",
        # 生活服务
        "把快递进度放到主屏",
        "桌面加个显示外卖进度的卡片",
        "主屏摆个能查航班动态的小组件",
        "帮我做个显示公交到站信息的桌面卡片",
        "小组件显示地铁换乘信息",
        "桌面放个路况信息面板",
        # 金融信息
        "负一屏挂个股票走势卡片",
        "帮我做个能看汇率的小组件",
        "桌面加个显示基金涨跌的卡片",
        "主屏摆个金价走势组件",
        "小组件展示持仓盈亏情况",
        # 系统状态
        "在桌面加个电量显示卡片",
        "主屏放个显示电池健康度的组件",
        "桌面摆个能看流量用量的卡片",
        "帮我做个显示话费余额的小组件",
        "锁屏加个网速监测挂件",
        "桌面卡片显示屏幕使用时间",
        # 通讯社交 / AI 生成
        "主屏摆个未读消息提醒组件",
        "桌面加个显示未读消息数的卡片",
        "帮我做个能看最近联系人的小组件",
        "主屏放个智能生成的每日摘要卡片",
        "桌面摆个展示助手建议的挂件",
    ),
    IntentCategory.CLARIFICATION_ANSWER: (
        "选择四乘四",
        "用 4x4 规格",
        "就这个规格",
        "四乘四",
        "选四乘四",
        "用四乘四",
        "就四乘四",
        "选 4x4",
        "4x4 吧",
        "按这个来",
        "就按这个",
        "默认规格",
        "用 2x4 规格",
        "两行四列",
    ),
}


def _normalize(text: str) -> str:
    """归一化文本：小写、去空白。"""
    return re.sub(r"\s+", "", text.lower())


# 共享词表（关键修正）：所有文本映射到同一 bigram 词表，向量才可比。
# v2：只由样本库生成，不再注入 widget 类型词（ADR-004）。
_VOCAB: tuple[str, ...] = tuple(
    sorted(
        {
            _normalize(text)[i : i + 2]
            for samples in _SAMPLES.values()
            for text in samples
            for i in range(len(_normalize(text)) - 1)
        }
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
