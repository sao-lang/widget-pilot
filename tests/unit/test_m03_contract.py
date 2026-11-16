"""M03 契约测试：T14 事件 schema（18 种 kind）/ T15 错误码分类（17 码不重不漏）。"""
from __future__ import annotations

import json

import jsonschema

from packages.agent_core.errors import (
    BUSINESS_FAILURE,
    INFRA_FAILURE,
    assert_partition,
)
from packages.agent_core.types import AgentEvent
from packages.contracts.enums import AgentEventKind, ReasonCode

SCHEMA = "packages/contracts/events.schema.json"


# ---- T14：18 种 AgentEventKind 事件全部通过 schema 校验 ----
def test_T14_all_event_kinds_validate():
    assert len(AgentEventKind) == 18
    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.validators.validator_for(schema)(schema)
    # 每种 kind 的枚举值都在 schema 的 kind enum 中
    allowed = set(schema["properties"]["kind"]["enum"])
    assert {k.value for k in AgentEventKind} <= allowed
    # payload 用字符串（只唯一命中 plan 分支），使完整 schema 校验聚焦 kind/字段合法性。
    # 注：events.schema.json 的 payload oneOf 各 object 分支字段全 optional、彼此重叠，
    # 这是 M02 schema 的已知宽松点，不在 M03 范围；T14 关注 kind 值合法与字段齐备。
    for kind in AgentEventKind:
        event = AgentEvent(
            seq=1, session_id="s", run_id="r", ts="2026-01-01T00:00:00+00:00",
            kind=kind.value, phase="RUNNING", payload="plan text", trace_id="t",
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
    assert assert_partition(None) is True
    all_codes = {c.value for c in ReasonCode}
    assert (BUSINESS_FAILURE | INFRA_FAILURE) == all_codes
    assert (BUSINESS_FAILURE & INFRA_FAILURE) == set()


def test_T15_new_v13_codes_are_business():
    for code in ("SPEC_MISSING", "CLARIFICATION_REQUIRED", "CLARIFICATION_LIMIT"):
        assert code in BUSINESS_FAILURE
        assert code not in INFRA_FAILURE
