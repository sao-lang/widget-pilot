"""M03 契约测试：T14 事件 schema（21 种 kind）/ T15 错误码分类（17 码不重不漏）。"""
from __future__ import annotations

import json

from jsonschema import validators

from packages.agent_core.errors import (
    BUSINESS_FAILURE,
    INFRA_FAILURE,
    assert_partition,
)
from packages.agent_core.types import AgentEvent, JsonValue
from packages.contracts.enums import AgentEventKind, ReasonCode

SCHEMA = "packages/contracts/events.schema.json"


# ---- T14：21 种 AgentEventKind 事件全部通过 schema 校验 ----
def test_T14_all_event_kinds_validate():
    # ADR-003 删 skill（17）；ADR-008 +reasoning（18）；ADR-007 +agent_message/review/delegate（21）
    assert len(AgentEventKind) == 21
    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    validator = validators.validator_for(schema)(schema)
    # 每种 kind 的枚举值都在 schema 的 kind enum 中
    allowed = set(schema["properties"]["kind"]["enum"])
    assert {k.value for k in AgentEventKind} <= allowed
    # M3（v1.8）：error/notice kind 的 discriminator 已生效（string 兜底移除，
    # 必填约束生效），故按 kind 给合法 payload，其余 kind 仍用字符串（命中 plan 兜底）。
    # T14 仍聚焦 kind 值合法与字段齐备，不再依赖事件级宽松兜底。
    def _payload_for(kind: AgentEventKind) -> JsonValue:
        """按 discriminator 要求提供合法 payload。"""
        if kind is AgentEventKind.ERROR:
            return {"code": "MODEL_ERROR", "evidenceRef": "evidence/x#sha256:abc"}
        if kind is AgentEventKind.NOTICE:
            return {"code": "SPEC_MISSING", "message": "请选择规格"}
        return "plan text"

    for kind in AgentEventKind:
        event = AgentEvent(
            seq=1, session_id="s", run_id="r", ts="2026-01-01T00:00:00+00:00",
            kind=kind.value, phase="RUNNING", payload=_payload_for(kind), trace_id="t",
        )
        validator.validate(event.to_dict())  # 不抛即通过


def test_T14_event_required_fields():
    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    required = set(schema["required"])
    assert {"seq", "sessionId", "runId", "ts", "kind", "phase", "payload", "traceId"} <= required


# ---- T15：17 个 ReasonCode 恰好归入 business/infra 一类，不重不漏 ----
def test_T15_reason_code_count():
    assert len(ReasonCode) == 17


def test_T15_partition_exhaustive_and_disjoint():
    assert assert_partition() is True
    all_codes = {c.value for c in ReasonCode}
    assert (BUSINESS_FAILURE | INFRA_FAILURE) == all_codes
    assert (BUSINESS_FAILURE & INFRA_FAILURE) == set()


def test_T15_new_v13_codes_are_business():
    for code in ("SPEC_MISSING", "CLARIFICATION_REQUIRED", "CLARIFICATION_LIMIT"):
        assert code in BUSINESS_FAILURE
        assert code not in INFRA_FAILURE
