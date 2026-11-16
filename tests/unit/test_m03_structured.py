"""M03 structured 层：T9 partial-JSON / T11 WidgetSpec / T12 manifest / repair 回灌。"""
from __future__ import annotations

import json

import pytest

from packages.agent_core.structured.partial import parse as partial_parse
from packages.agent_core.structured.repair import feedback
from packages.agent_core.structured.schema import validate, validate_manifest
from packages.agent_core.types import RepairError

SCHEMA_DIR = "packages/contracts"


def load_schema(name: str) -> dict:
    with open(f"{SCHEMA_DIR}/{name}", encoding="utf-8") as f:
        return json.load(f)


def valid_spec() -> dict:
    return {
        "spec_version": "1.0", "widget_id": "w1", "grid": "4x4", "widget_type": "calendar",
        "intent": "月视图日历", "attachments": [],
        "layout_slots": [{"slot": "main", "content": "calendar"}],
        "capabilities": ["date"],
        "data_contract": {"sources": ["time"], "storage_namespace": "u1/w1"},
        "style": {}, "constraints": {"forbidden": [], "max_elements": 20},
        "language": "tsx", "react_version": "19", "import_allowlist": ["react", "@widget/bridge"],
        "export_contract": {"default": "Widget"},
        "props_contract": ["grid", "theme", "locale", "tick", "data"],
    }


# ---- T9：partial JSON 容错（未闭合字符串 / 括号 / 末尾字符串字段）----
@pytest.mark.parametrize("text", [
    '{"spec_version": "1.0", "widget_id": "w1"',
    '{"intent": "月视图',
    '{"capabilities": ["date", "tim',
    '{"a": {"b": 1',
])
def test_T9_partial_parse_never_raises(text):
    obj = partial_parse(text)
    assert obj.raw == text
    assert isinstance(obj.value, dict)
    assert obj.complete is False


def test_T9_partial_parse_complete_json():
    obj = partial_parse('{"a": 1, "b": "x"}')
    assert obj.complete is True
    assert obj.value == {"a": 1, "b": "x"}


# ---- T11：WidgetSpec schema 严格校验 ----
def test_T11_valid_spec_passes():
    result = validate(json.dumps(valid_spec()), load_schema("widget_spec.schema.json"))
    assert result.ok is True
    assert result.data == valid_spec()


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(grid="8x8"),
    lambda s: s["export_contract"].update(default="Evil"),
    lambda s: s.update(capabilities=["date", "unknown_cap"]),
    lambda s: s.update(import_allowlist=["react", "eval"]),
])
def test_T11_invalid_spec_rejected(mutate):
    spec = valid_spec()
    mutate(spec)
    result = validate(json.dumps(spec), load_schema("widget_spec.schema.json"))
    assert result.ok is False
    assert result.errors  # 结构化错误非空


# ---- T12：manifest.capabilities 与 spec.capabilities 不一致 → CONTRACT_MISMATCH ----
def test_T12_manifest_capability_mismatch():
    spec = valid_spec()
    manifest = {"spec_version": "1.0", "capabilities": ["timer"]}
    result = validate_manifest(spec, manifest)
    assert result.ok is False
    assert result.code == "CONTRACT_MISMATCH"
    assert any("capabilities" in e.field for e in result.errors)


def test_T12_manifest_match():
    spec = valid_spec()
    manifest = {"spec_version": "1.0", "capabilities": ["date"]}
    assert validate_manifest(spec, manifest).ok is True


# ---- repair.feedback：结构化回灌 {field, expected, got} ----
def test_repair_feedback_writes_signal_and_increments(sample_state):
    errors = [RepairError(field="grid", expected="4x4", got="8x8")]
    out = feedback(sample_state, [e.as_dict() for e in errors])
    assert out.repair_count == sample_state.repair_count + 1
    assert (out.spec or {})["__signal"] == "CALL_MODEL"  # 回边完成，下轮重生成
    assert (out.spec or {})["__errors"] == [{"field": "grid", "expected": "4x4", "got": "8x8"}]
    assert out.last_error_fingerprint is not None
