"""跨模块共享的确定类型（手工维护，非生成物）。

集中定义散落在 services/tests 的 JSON 边界与信封结构，取代裸 ``Any`` /
``object`` / ``dict[str, object]``。单一事实源：JSON 值别名在 agent_core，
本模块 re-export 并补充 API 信封与任务字段等契约结构。
新增字段须同步对应契约源（见 ``contracts:check``）。
"""

from __future__ import annotations

from typing import TypedDict

from packages.agent_core.types import JsonObject, JsonValue

__all__ = ["ApiEnvelope", "JsonObject", "JsonValue", "TaskFields"]

type ApiEnvelope = dict[str, JsonValue]
"""统一响应信封 ``ApiResponse``（code/message/traceId/retryable/evidenceRef?/data?）。

用 JsonValue 值域而非裸 object：契约 required 五字段齐全，值均为 JSON 标量或
嵌套 JSON；FastAPI 以 response_model 校验确定结构（已实测 dict[str, JsonValue]
可正常注册与序列化）。
"""


class TaskFields(TypedDict):
    """dispatcher 注入校验链的扁平字段（键集合确定，源于 Task dict）。

    Note:
        Task 来自 Redis Streams 反序列化（JSON 边界），取值先经 ``_as_str``
        收窄为字符串；attachment_ids 是附件 id 列表。
    """

    session_id: str
    run_id: str
    description: str
    grid: str | None
    attachment_ids: list[str]
    clarified_grid: str | None
    clarification_reply: str
    user_id: str
    """会话 owner（M07 B1：偏好读取的租户隔离键）。"""
