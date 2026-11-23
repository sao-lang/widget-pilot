"""packages/sdk/python —— 自动生成物（M02 §7.2），禁止手改。"""
from __future__ import annotations

from pydantic import BaseModel

# JSON 边界具名别名：生成物自包含（SDK 独立分发，不依赖 agent_core）。
type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class AgentEvent(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    seq: int
    sessionId: str
    runId: str
    ts: str
    kind: str
    phase: str
    payload: JsonValue
    traceId: str
    taskId: str | None = None
    taskIndex: int | None = None
    turn: int | None = None


class WidgetSpec(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    spec_version: str
    widget_id: str
    grid: str
    widget_type: str
    intent: str
    attachments: list[JsonValue]
    layout_slots: list[JsonValue]
    capabilities: list[JsonValue]
    data_contract: JsonObject
    style: JsonObject
    constraints: JsonObject
    language: str
    react_version: str
    import_allowlist: str
    export_contract: JsonObject
    props_contract: str


class WidgetBundle(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    bundle_id: str
    user_id: str
    session_id: str
    spec: WidgetSpec
    source: JsonObject
    compiled: JsonObject
    manifest: JsonObject
    static_check: JsonObject
    sandbox_test: JsonObject
    state: str
    hash: str
    created_at: str


class WidgetRuntime(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    card: JsonObject
    iframe: JsonObject
    bridge_messages: str
    capability_bridge: list[JsonValue]
    limits: JsonObject


class ApiResponse(BaseModel):
    """统一响应（源: openapi.yaml components.schemas.ApiResponse）"""
    code: str
    message: str
    traceId: str
    retryable: bool
    evidenceRef: str | None = None
    data: JsonValue | None = None

