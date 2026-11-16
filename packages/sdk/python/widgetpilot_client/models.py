"""packages/sdk/python —— 自动生成物（M02 §7.2），禁止手改。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

class AgentEvent(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    seq: int
    sessionId: str
    runId: str
    ts: str
    kind: Any
    phase: Any
    payload: Any
    traceId: str

class WidgetSpec(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    spec_version: str
    widget_id: str
    grid: Any
    widget_type: Any
    intent: str
    attachments: list[Any]
    layout_slots: list[Any]
    capabilities: list[Any]
    data_contract: dict[str, Any]
    style: dict[str, Any]
    constraints: dict[str, Any]
    language: Any
    react_version: str
    import_allowlist: Any
    export_contract: dict[str, Any]
    props_contract: Any

class WidgetBundle(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    bundle_id: str
    user_id: str
    session_id: str
    spec: widget_spec.schema.json
    source: dict[str, Any]
    compiled: dict[str, Any]
    manifest: dict[str, Any]
    static_check: dict[str, Any]
    sandbox_test: dict[str, Any]
    state: Any
    hash: str
    created_at: str

class WidgetRuntime(BaseModel):
    """自动生成（禁止手改）；源: schema"""
    card: dict[str, Any]
    iframe: dict[str, Any]
    bridge_messages: Any
    capability_bridge: list[Any]
    limits: dict[str, Any]
