"""M08 出护栏单测：A1/A2/A3/A18 + I1/I2/I5。先红后绿。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from packages.contracts.enums import ReasonCode
from services.guardrails.contract_check import compute_spec_hash
from services.guardrails.output_guard import check_output_guard
from services.guardrails.output_ports import OutputGuardReport
from tests.support.m08 import FakeSourceStore, RecordingModel

if TYPE_CHECKING:
    from packages.contracts.types import JsonObject, JsonValue

_VALID_TSX = """import React from "react";
import bridge from "@widget/bridge";
export default function Widget() {
  return <div style={{width: 160, height: 160}}>ok</div>;
}
"""


def _spec(**overrides: JsonValue) -> JsonObject:
    spec: JsonObject = {
        "spec_version": "v1",
        "widget_id": "w1",
        "grid": "2x2",
        "widget_type": "custom",
        "intent": "桌面月视图日历，能翻月",
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


def _guard(source: str, **spec_overrides: JsonValue) -> tuple[OutputGuardReport, RecordingModel]:
    spec = _spec(**spec_overrides)
    model = RecordingModel()
    store = FakeSourceStore({"code/s/run/widget.tsx": source.encode()})
    report = check_output_guard(
        spec,
        "code/s/run/widget.tsx",
        _manifest(spec),
        source_store=store,
    )
    return report, model


def test_a1_compliant_output_passes_with_zero_model_calls() -> None:
    """A1/I2：合规 TSX 六判据全过，fake model 调用次数为 0。"""
    report, model = _guard(_VALID_TSX)
    assert report.passed is True
    assert report.hit is False
    assert report.violations == ()
    assert report.reason_code is None
    assert model.calls == 0


def test_i1_plain_text_is_not_treated_as_code() -> None:
    """I1：纯文本不是 TSX AST、无默认导出 Widget，必须拒绝。"""
    report, _ = _guard("this is not code")
    assert report.passed is False
    assert report.judge == "schema"
    assert report.reason_code == ReasonCode.GUARD_OUTPUT_HIT


def test_a2_fetch_call_is_blocked_without_raw_source_in_evidence() -> None:
    """A2：fetch 外联命中；原文 URL 不进证据（ADR-005）。"""
    report, _ = _guard(_VALID_TSX + 'fetch("https://evil.example/path");\n')
    assert report.passed is False
    assert report.judge == "forbidden_content"
    assert report.reason_code == ReasonCode.GUARD_OUTPUT_HIT
    assert any("fetch" in v.rule for v in report.violations)
    assert all(len(v.snippet_hash) == 64 for v in report.violations)
    assert "https://evil.example" not in repr(report)


def test_a3_eval_and_unknown_import_are_blocked() -> None:
    """A3：eval 与白名单外 import 命中 forbidden_content。"""
    source = 'import fs from "fs";\n' + _VALID_TSX + 'eval("1+1");\n'
    report, _ = _guard(source)
    assert report.passed is False
    assert report.judge == "forbidden_content"
    rules = " ".join(v.rule for v in report.violations)
    assert "eval" in rules
    assert "import_not_allowed" in rules


def test_capability_allowlist_mismatch_denied() -> None:
    """R2：manifest 多声明 timer → CAPABILITY_DENIED。"""
    spec = _spec()
    model = RecordingModel()
    store = FakeSourceStore({"code/s/run/widget.tsx": _VALID_TSX.encode()})
    report = check_output_guard(
        spec,
        "code/s/run/widget.tsx",
        _manifest(spec, capabilities=["date", "timer"]),
        source_store=store,
    )
    assert report.passed is False
    assert report.judge == "capability_allowlist"
    assert report.reason_code == ReasonCode.CAPABILITY_DENIED
    assert model.calls == 0


def test_invalid_capability_value_denied() -> None:
    """R2：manifest 出现 Capability 枚举外的值也要拒绝。"""
    spec = _spec()
    store = FakeSourceStore({"code/s/run/widget.tsx": _VALID_TSX.encode()})
    report = check_output_guard(
        spec,
        "code/s/run/widget.tsx",
        _manifest(spec, capabilities=["date", "root"]),
        source_store=store,
    )
    assert report.passed is False
    assert report.reason_code == ReasonCode.CAPABILITY_DENIED


def test_a18_valid_email_pii_hit_without_raw_in_report() -> None:
    """A18：真值有效的邮箱命中 PII，报告不含明文。"""
    report, _ = _guard(_VALID_TSX, intent="桌面日历 zhangsan@example.com 能翻月")
    assert report.passed is False
    assert report.judge == "pii"
    assert report.reason_code == ReasonCode.GUARD_OUTPUT_HIT
    assert "zhangsan@example.com" not in repr(report)


def test_pii_shape_but_invalid_phone_not_blocked() -> None:
    """PII 真值校验：形状像但非有效号不误报（validators.py 复用）。"""
    report, _ = _guard(_VALID_TSX, intent="桌面日历 12345678901 能翻月")
    assert report.passed is True


def test_spec_conflict_2x1_month_view() -> None:
    """规格一致性：2x1 + 月视图在 spec 层即 SPEC_CONFLICT。"""
    report, _ = _guard(_VALID_TSX, grid="2x1")
    assert report.passed is False
    assert report.judge == "spec_consistency"
    assert report.reason_code == ReasonCode.SPEC_CONFLICT


def test_intent_match_requires_feature_slot() -> None:
    """ADR-004 去类型化：不靠 widget_type，按开放特征槽位判描述符合度。"""
    report, _ = _guard(
        _VALID_TSX,
        grid="4x4",
        layout_slots=[{"slot": "blank", "content": "占位"}],
    )
    assert report.passed is False
    assert report.judge == "intent_match"


def test_default_export_must_be_widget() -> None:
    """结构化 schema：默认导出名非 Widget 拒绝。"""
    source = _VALID_TSX.replace("Widget()", "NotWidget()")
    report, _ = _guard(source)
    assert report.passed is False
    assert report.judge == "schema"


def test_i5_no_eval_exec_or_subprocess_in_guard_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """I5：出护栏路径不执行模型代码，不调用 eval/exec/子进程。"""
    import builtins

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("guard path evaluated generated code")

    monkeypatch.setattr(builtins, "eval", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)
    report, _ = _guard(_VALID_TSX)
    assert report.passed is True


def test_a10_unknown_device_api_blocked_in_output_guard() -> None:
    """ADR-010 §3.2：出护栏拦截白名单外设备 API（进护栏只扫描述，这里扫模型源码）。"""
    source = _VALID_TSX + 'window.widgetDevice.getRootAccess();\n'
    report, _ = _guard(source)
    assert report.passed is False
    assert report.reason_code == ReasonCode.GUARD_OUTPUT_HIT
    rules = " ".join(v.rule for v in report.violations)
    assert "dangerous_api:device.getRootAccess" in rules
    assert all(len(v.snippet_hash) == 64 for v in report.violations)
    assert "window.widgetDevice.getRootAccess()" not in repr(report)  # 完整源码片段不进证据


def test_a11_dynamic_device_api_construction_blocked_in_output_guard() -> None:
    """ADR-010 §3.2：动态构造 API 名 device[name]() 在出护栏被形状层拦截。"""
    source = _VALID_TSX + 'const n = "getLocation"; device[n]();\n'
    report, _ = _guard(source)
    assert report.passed is False
    rules = " ".join(v.rule for v in report.violations)
    assert "dynamic_api:constructed-name" in rules


def test_a12_legit_device_api_passes_output_guard() -> None:
    """白名单内设备 API 不误伤（零回归）。"""
    source = _VALID_TSX + 'window.widgetDevice.getBattery();\n'
    report, _ = _guard(source)
    assert report.passed is True
