"""M07 记忆与上下文装配的可注入端口（v1.6）。

purity-exempt-file: 本文件全部为 @runtime_checkable Protocol 接口签名；方法体 `...` 是契约声明非占位，所有参数已在各方法 Args 段点名说明。


生产实现：``PreferenceStore`` / ``ProcedureStore`` 由 PG 落地（``pg_stores.py``）；
四个装配端口由 M07 装配层从各模块真实数据读取。测试通过显式注入 mock 满足协议。
**端口未注入时对应槽省略，禁止硬编码占位串冒充真实数据；生产关键端口缺失 fail-fast**（ADR-002 / v1.6 装配纪律修订）。

端口只定义鸭子协议，不 import 任何具体实现，避免循环依赖。协议方法体统一用 ``...``
并在 docstring 写明 ``Args`` / ``Returns``（意图注释，供静态扫描识别为非占位）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.agent_core.types import Candidate, JsonValue
from services.orchestrator.memory.types import (
    PreferenceEvent,
    PreferenceField,
    PreferenceSource,
    UserPreference,
    UserProcedure,
)


@runtime_checkable
class PreferenceStorePort(Protocol):
    """语义/情节记忆持久化端口（真实实现 = PG；测试显式注入替身）。"""

    def get(self, user_id: str) -> UserPreference:
        """读取用户偏好；无记录返回空偏好（version=1，fields 为空）。

        Args:
            user_id: 租户隔离键，只返回该用户的偏好。

        Returns:
            用户偏好聚合；无记录时空 fields 而非 None（调用方无需空值分支）。
        """
        ...

    def apply(
        self,
        user_id: str,
        session_id: str | None,
        field_name: PreferenceField,
        new_value: JsonValue,
        source: PreferenceSource,
        trace_id: str,
    ) -> tuple[UserPreference, PreferenceEvent]:
        """四操作写入：返回写入后的偏好与刚追加的事件（含四操作决策）。

        Args:
            user_id: 租户隔离键。
            session_id: 触发会话；显式写入可为空。
            field_name: 被写入的白名单字段。
            new_value: 字段新值（JSON 边界，确定性短值）。
            source: 写入信号来源（决定固定权重）。
            trace_id: 链路追踪标识，写进事件供审计。

        Returns:
            （写入后偏好，刚追加的情节事件）。
        """
        ...

    def clear(
        self,
        user_id: str,
        fields: list[PreferenceField] | None,
        trace_id: str,
    ) -> UserPreference:
        """清除（全字段或指定字段）：值置空、版本 +1、事件标 revoked_at。

        Args:
            user_id: 租户隔离键。
            fields: 待清除字段；None 表示全字段。
            trace_id: 链路追踪标识，写进事件供审计。

        Returns:
            清除后的偏好（字段为空、version 已递增）。
        """
        ...


@runtime_checkable
class ProcedureStorePort(Protocol):
    """程序记忆持久化端口（真实实现 = PG）。"""

    def get_for_type(self, user_id: str, widget_type: str) -> list[UserProcedure]:
        """按用户 + 控件类型读取采纳记录；无记录返回空列表。

        Args:
            user_id: 租户隔离键。
            widget_type: 目标控件类型，匹配采纳记录。

        Returns:
            该用户该类型的采纳记录列表；无记录为空列表。
        """
        ...

    def record_adopt(
        self,
        user_id: str,
        recipe_id: str,
        widget_type: str,
        trace_id: str,
    ) -> UserProcedure:
        """显式采纳 +1（或首次插入），版本 +1。

        Args:
            user_id: 租户隔离键。
            recipe_id: 被采纳的规程句柄。
            widget_type: 采纳控件的类型。
            trace_id: 链路追踪标识。

        Returns:
            更新后的程序记忆记录（采纳次数已递增）。
        """
        ...

    def record_revoke(self, user_id: str, recipe_id: str) -> None:
        """删除/回滚采纳：adopt_count -1；归零则失效不删行，版本 +1。

        Args:
            user_id: 租户隔离键。
            recipe_id: 被撤销采纳的规程句柄。

        Returns:
            无返回；撤销结果通过再次 get_for_type 可见。
        """
        ...


@runtime_checkable
class PreferencePort(Protocol):
    """上下文第 5 槽读取端口：返回高置信、衰减排序后的偏好注入文本。"""

    def injection_text(self, user_id: str, max_tokens: int) -> str:
        """生成用户记忆槽正文；无高置信字段返回空串（槽位可省略）。

        Args:
            user_id: 租户隔离键。
            max_tokens: 该槽可用 token 上限，实现须按此截断。

        Returns:
            可注入的偏好文本；无高置信内容时为空串。
        """
        ...


@runtime_checkable
class ProcedurePort(Protocol):
    """上下文第 2 槽读取端口：返回程序记忆采纳软提示（不写 prompt 前缀，I-P1）。"""

    def hint_text(self, user_id: str, widget_type: str) -> str:
        """生成采纳软提示；``adopt_count < 2`` 返回空串。

        Args:
            user_id: 租户隔离键。
            widget_type: 当前控件类型，匹配采纳记录。

        Returns:
            软提示文本；未达阈值时为空串，装配层据此省略该槽。
        """
        ...


@runtime_checkable
class RetrievalPort(Protocol):
    """上下文第 3 槽检索端口（真实实现由 M10 注入，见 M10 §9.0b）。"""

    def retrieve(self, query: str, top_k: int) -> list[Candidate]:
        """返回检索候选（doc_id/text/score/source）；无结果返回空列表。

        Args:
            query: 检索查询串（装配层取当前用户意图）。
            top_k: 候选数量上限。

        Returns:
            候选列表；空列表表示无结果（转 notice + HUMAN_REQUIRED）。
        """
        ...


@runtime_checkable
class AttachmentInsightPort(Protocol):
    """上下文第 4 槽附件洞察端口（真实实现由 M05 注入，见 M05 §9.0）。"""

    def insights(self, attachment_refs: list[str]) -> list[str]:
        """按附件引用返回洞察文本；>20k token 只返回 object_key + 预览。

        Args:
            attachment_refs: 附件引用键列表（对象存储引用）。

        Returns:
            洞察文本列表；无附件或无洞察时为空列表。
        """
        ...


@runtime_checkable
class FactSummaryStorePort(Protocol):
    """ADR-009 会话事实摘要持久化端口（真实实现 = PG；测试显式注入替身）。"""

    def latest(self, session_id: str) -> tuple[int, str]:
        """读取会话最新事实摘要（turn_index + 渲染文本）。

        Args:
            session_id: 会话标识（租户锚点）。

        Returns:
            (turn_index, rendered)；无记录时 (0, "") 表示无事实。
        """
        ...

    def append(
        self,
        session_id: str,
        turn_index: int,
        payload: JsonValue,
        rendered: str,
    ) -> None:
        """追加一条事实摘要（session+turn 唯一；PG 失败抛异常 fail-closed）。

        Args:
            session_id: 会话标识。
            turn_index: 迭代序号（1-based）。
            payload: FactSummary 的 JSON 可序列化形态（白名单字段）。
            rendered: 供结论槽追加的渲染文本。

        Returns:
            None（写失败以异常上报，不返回 False）。
        """
        ...
