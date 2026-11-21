"""
purity-exempt-file: 本文件为 M03 侧上下文装配 Protocol 契约镜像，方法体 `...` 均为接口声明非占位，参数在 Args 段说明。
M03 侧上下文装配端口协议镜像（v1.6）。

与 ``services/orchestrator/memory/ports.py`` 的四个装配端口**鸭子兼容**：
方法名、参数、返回形态一致，生产实现（PG / M10 / M05）经 M07 装配层显式注入，
测试注入 mock。本模块只定义结构化协议，不 import 任何 services 实现——避免
``packages``（内核层）反向依赖 ``services``（业务层）的分层反转。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.agent_core.types import Candidate

__all__ = [
    "AttachmentInsightPort",
    "PreferencePort",
    "ProcedurePort",
    "RetrievalPort",
]


@runtime_checkable
class PreferencePort(Protocol):
    """用户记忆槽读取端口：返回高置信、衰减排序后的偏好注入文本。

    对应 services 层同名协议：``injection_text`` 无高置信字段时返回空串，
    装配层据此省略该槽；本内核不实现门禁逻辑（由端口实现负责）。
    """

    def injection_text(self, user_id: str, max_tokens: int) -> str:
        """生成用户记忆槽正文；无高置信字段返回空串。

        Args:
            user_id: 租户隔离的用户标识。
            max_tokens: 该槽可用 token 上限，端口须在此内生成正文。

        Returns:
            可注入的偏好文本；无高置信内容时为空串。
        """
        ...


@runtime_checkable
class ProcedurePort(Protocol):
    """规程软提示端口：返回程序记忆采纳提示（D-14，非规程正文）。

    对应 services 层同名协议：``adopt_count < 2`` 时 ``hint_text`` 返回空串，
    装配层只在非空时追加该槽，且不写 system 前缀（I-P1，保 caching）。
    """

    def hint_text(self, user_id: str, widget_type: str) -> str:
        """生成采纳软提示；采纳次数低于阈值返回空串。

        Args:
            user_id: 租户隔离的用户标识。
            widget_type: 当前控件类型，用于匹配采纳记录。

        Returns:
            软提示文本；未达 ``PROCEDURE_ADOPT_MIN`` 时为空串。
        """
        ...


@runtime_checkable
class RetrievalPort(Protocol):
    """检索片段槽端口：返回已召回候选，由内核完成 A″ 流水线后注入。

    对应 services 层同名协议（services 侧返回 ``list[object]``）；本侧用
    :class:`~packages.agent_core.types.Candidate` 表达候选的统一形态
    （doc_id / text / score / source），两者按鸭子类型兼容。
    """

    def retrieve(self, query: str, top_k: int) -> list[Candidate]:
        """按查询返回检索候选；无结果返回空列表。

        Args:
            query: 检索查询串（装配层取当前用户意图）。
            top_k: 期望返回的候选数量上限。

        Returns:
            候选列表；空列表表示无检索结果（不编造、转 notice）。
        """
        ...


@runtime_checkable
class AttachmentInsightPort(Protocol):
    """附件洞察槽端口：按附件引用返回洞察文本（M05 AttachmentInsight）。

    对应 services 层同名协议：大于 20k token 的洞察由端口侧只返回
    object_key + 预览，装配层原样注入、不解析为生成指令。
    """

    def insights(self, attachment_refs: list[str]) -> list[str]:
        """按附件引用返回洞察文本列表。

        Args:
            attachment_refs: 附件引用键列表（对象存储 key 或 data URL）。

        Returns:
            洞察文本列表；无附件或无洞察时为空列表。
        """
        ...
