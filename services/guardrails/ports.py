"""M06 四级路由可注入端口（ADR-001 D-8 / 设计文档 §6.2 / v1.5 接线点）。

【本模块已做的部分】级联判定、升级判据、留证、默认零依赖实现、Protocol 契约。
【留待其他模块的部分】L2 embedding 模型（M20）、L3 低档端点（M20/M07）、
L4 主 LLM 端点（M07）——分别按下面三个 Protocol 注入，本模块不实作、不持凭证。

接线规则（§2.3 / §3.1）：
- 注入实现类 → 用真实能力；未注入 → 用默认零依赖实现并告警（不静默跳过）。
- 端点抛错/不可用 → 按结构化判据降级到下一级并留归因，绝不跳过判定放行。
- 拒绝类（OUT_OF_SCOPE / 注入 / PII）恒出自 L1，L2/L3/L4 绝不产生拒绝结论。

对接步骤见 docs/runbook/m06-docker-and-model-testing.md §3。
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol, runtime_checkable

from services.guardrails.telemetry import IntentRouteLogRow, NeedSkillLogRow
from services.guardrails.types import Embedding, LowTierResult


@runtime_checkable
class Embedder(Protocol):
    """L2 语义路由端口（§6.2）：本地 embedding 推理 + 相似度。

    真实实现由 M20 Provider 抽象（embed 端点）注入；默认实现 =
    ``DefaultEmbedder``（纯代码字符 bigram，零依赖可离线）。
    """

    def embed(self, text: str) -> Embedding:
        """把文本映射为向量。"""
        ...

    def similarity(self, a: Embedding, b: Embedding) -> float:
        """计算两向量相似度（默认实现为余弦）。"""
        ...


@runtime_checkable
class IntentClassifier(Protocol):
    """L3 小模型分类器端口（§6.2）：低档端点分类。

    真实实现由 M20/M07 经 ModelGateway 低档档位注入；默认实现 =
    ``RuleBasedClassifier``（返回不可解析，触发升级）。
    """

    def classify(self, text: str, max_tokens: int) -> LowTierResult:
        """对文本分类；输出必须可解析才是有效裁定。"""
        ...


@runtime_checkable
class IntentGateway(Protocol):
    """L4 主 LLM 兜底端口（v1.5 补齐：设计原有 L4 但无水口契约）。

    真实实现由 M07 ``ModelGateway`` 前沿档位注入；默认实现 =
    ``StructuredFallback``（不调用模型，按白名单方向兜底归类）。
    禁止把 L4 用作安全裁判——本端口只允许返回放行方向意图。
    """

    def resolve(self, text: str, max_tokens: int) -> LowTierResult:
        """对文本做最后兜底裁定；输出必须可解析。"""
        ...


@runtime_checkable
class IntentRouteLogStorePort(Protocol):
    """意图裁定日志写入口：内存实现与 PG 实现共同满足（A3 接线）。"""

    def append(self, row: IntentRouteLogRow) -> None: ...

    def stats(self) -> dict[str, float]: ...


@runtime_checkable
class NeedSkillLogStorePort(Protocol):
    """技能决策日志写入口：内存实现与 PG 实现共同满足（A3 接线）。"""

    def append(self, row: NeedSkillLogRow) -> None: ...

    def decisions(self) -> Counter: ...
