"""M08 契约校验单测：A4/A5 + I3/I4（不一致不进沙箱）。"""

from __future__ import annotations

import json
from typing import cast

from packages.agent_core.types import JsonObject, JsonValue
from packages.contracts.enums import ReasonCode
from services.guardrails.contract_check import check_contract, compute_spec_hash
from services.guardrails.static_scan import parse_tsx

_GOOD_TSX = """import React from "react";
import bridge from "@widget/bridge";
export default function Widget() {
  bridge.call("date");
  return <div/>;
}
"""


def _spec(**overrides: JsonValue) -> JsonObject:
    spec: JsonObject = {
        "spec_version": "v1",
        "widget_id": "w1",
        "grid": "2x2",
        "widget_type": "custom",
        "intent": "桌面日历，能翻月",
        "attachments": [],
        "layout_slots": [{"slot": "month", "content": "月视图"}],
        "capabilities": ["date"],
        "data_contract": {"sources": ["time"], "storage_namespace": "u/w1"},
        "style": {},
        "constraints": {"forbidden": [], "max_elements": 16},
        "language": "tsx",
        "react_version": "19.0.0",
        "import_allowlist": ["react", "@widget/bridge"],
        "export_contract": {"default": "Widget"},
        "props_contract": ["grid", "theme", "locale", "tick", "data"],
    }
    spec.update(overrides)
    return spec


def _manifest(spec: JsonObject, **overrides: JsonValue) -> JsonObject:
    manifest: JsonObject = {
        "spec_version": "v1",
        "spec_hash": compute_spec_hash(spec),
        "grid": spec["grid"],
        "widget_type": "custom",
        "capabilities": list(cast(list[str], spec["capabilities"])),
        "import_allowlist": ["react", "@widget/bridge"],
        "exports": {"default": "Widget"},
        "props": ["grid", "theme", "locale", "tick", "data"],
        "recipe_version": "r1",
        "retrieval_snapshot": "s1",
        "model": "m",
        "prompt_version": "p1",
        "model_tier": "cheap",
    }
    manifest.update(overrides)
    return manifest


def test_valid_contract_passes() -> None:
    """三方一致 + hash 一致 → passed=True。"""
    spec = _spec()
    report = check_contract(spec, _manifest(spec), parse_tsx(_GOOD_TSX))
    assert report.passed is True
    assert report.violations == ()
    assert report.reason_code is None
    assert report.spec_hash == compute_spec_hash(spec)


def test_a4_capability_mismatch_fails() -> None:
    """spec↔manifest 能力不一致 → CONTRACT_MISMATCH。"""
    spec = _spec()
    report = check_contract(
        spec, _manifest(spec, capabilities=["date", "timer"]), parse_tsx(_GOOD_TSX)
    )
    assert report.passed is False
    assert report.reason_code == ReasonCode.CONTRACT_MISMATCH
    assert any("timer" in v for v in report.violations)


def test_a5_tampered_spec_hash_fails() -> None:
    """spec_hash 被篡改 → CONTRACT_MISMATCH。"""
    spec = _spec()
    report = check_contract(
        spec, _manifest(spec, spec_hash="0" * 64), parse_tsx(_GOOD_TSX)
    )
    assert report.passed is False
    assert report.reason_code == ReasonCode.CONTRACT_MISMATCH
    assert any("spec_hash" in v for v in report.violations)


def test_undeclared_capability_call_fails() -> None:
    """代码调用未声明能力 → CONTRACT_MISMATCH。"""
    spec = _spec()
    source = _GOOD_TSX.replace('bridge.call("date")', 'bridge.call("timer")')
    report = check_contract(spec, _manifest(spec), parse_tsx(source))
    assert report.passed is False
    assert any("timer" in v for v in report.violations)


def test_unknown_import_fails() -> None:
    """白名单外 import → CONTRACT_MISMATCH。"""
    spec = _spec()
    source = 'import fs from "fs";\n' + _GOOD_TSX
    report = check_contract(spec, _manifest(spec), parse_tsx(source))
    assert report.passed is False
    assert any("fs" in v for v in report.violations)


def test_grid_mismatch_fails() -> None:
    """manifest grid 与 spec 不一致 → CONTRACT_MISMATCH。"""
    spec = _spec()
    report = check_contract(spec, _manifest(spec, grid="2x4"), parse_tsx(_GOOD_TSX))
    assert report.passed is False
    assert any("grid" in v for v in report.violations)


def test_manifest_missing_required_field_fails() -> None:
    """manifest 必填字段缺失 → CONTRACT_MISMATCH（不猜缺省值）。"""
    spec = _spec()
    manifest = _manifest(spec)
    del manifest["recipe_version"]
    report = check_contract(spec, manifest, parse_tsx(_GOOD_TSX))
    assert report.passed is False
    assert any("recipe_version" in v for v in report.violations)


def test_spec_hash_is_canonical_regardless_of_key_order() -> None:
    """canonical_json 排序键：字段顺序不同但内容相同 → hash 相同。"""
    spec = _spec()
    reversed_spec = dict(reversed(list(spec.items())))
    assert compute_spec_hash(spec) == compute_spec_hash(reversed_spec)
    assert json.dumps(spec, sort_keys=True) is not None
