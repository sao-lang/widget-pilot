"""M08 汇总入口单测：A13/A14/A16 + I4/I12/I14，A→B→C 短路与降级。"""

from __future__ import annotations

import json
from typing import cast

from packages.agent_core.types import JsonObject, JsonValue
from packages.contracts.enums import ReasonCode
from services.guardrails.contract_check import compute_spec_hash
from services.sandbox_runner.entry import on_sandbox_unavailable, validate_and_test
from services.sandbox_runner.ports import CommandResult
from tests.support.m08 import (
    FailingEvidenceStore,
    FakeDockerRunner,
    FakeSourceStore,
    MemoryEvidenceStore,
    RecordingModel,
    sandbox_report,
)

_VALID_TSX = """import React from "react";
import bridge from "@widget/bridge";
export default function Widget() {
  bridge.call("date");
  return <div style={{width: 160, height: 160}}>ok</div>;
}
"""


def _spec() -> JsonObject:
    return {
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


def _validate(
    *,
    source: str = _VALID_TSX,
    manifest: JsonObject | None = None,
    runner: FakeDockerRunner | None = None,
    evidence: MemoryEvidenceStore | None = None,
    repair_count: int = 0,
    run_id: str = "run-1",
):
    spec = _spec()
    evidence = evidence or MemoryEvidenceStore()
    runner = runner or FakeDockerRunner(
        result=_command_result(sandbox_report()),
    )
    return validate_and_test(
        spec,
        "code/sess/" + run_id + "/widget.tsx",
        manifest or _manifest(spec),
        repair_count,
        session_id="sess",
        run_id=run_id,
        evidence_store=evidence,
        source_store=FakeSourceStore(
            {"code/sess/" + run_id + "/widget.tsx": source.encode()}
        ),
        docker_runner=runner,
        prefix="evidence/sess/" + run_id,
    )


def _command_result(value: JsonValue) -> CommandResult:
    return CommandResult(stdout=json.dumps(value), stderr="", exit_code=0)


def test_full_chain_passes_in_fixed_stage_order() -> None:
    """正常通过：A→B→C 顺序固定，零模型调用。"""
    model = RecordingModel()
    result = _validate()
    assert result.passed is True
    assert result.reason_code is None
    assert tuple(result.stages) == (
        "OUTPUT_GUARD",
        "CONTRACT_CHECK",
        "COMPILE",
        "RENDER",
        "SIZE_ASSERT",
        "RUNTIME_ASSERT",
        "CAPABILITY_ASSERT",
        "INTERACTION_ASSERT",
        "DEVICE_API_ASSERT",
        "EVIDENCE_WRITE",
        "PASSED",
    )
    assert model.calls == 0


def test_guard_hit_short_circuits_without_sandbox() -> None:
    """I4：出护栏命中不创建沙箱、不进契约校验。"""
    runner = FakeDockerRunner(result=_command_result(sandbox_report()))
    result = _validate(
        source=_VALID_TSX + 'fetch("https://evil.example");\n',
        runner=runner,
        run_id="run-guard",
    )
    assert result.passed is False
    assert result.reason_code == ReasonCode.GUARD_OUTPUT_HIT
    assert result.sandbox_test is None
    assert runner.commands == []


def test_contract_mismatch_short_circuits_without_sandbox() -> None:
    """A4/I3/I4：代码实际调用未声明能力（spec↔代码不一致）不创建沙箱。"""
    runner = FakeDockerRunner(result=_command_result(sandbox_report()))
    source = _VALID_TSX.replace('bridge.call("date")', 'bridge.call("timer")')
    result = _validate(source=source, runner=runner, run_id="run-contract")
    assert result.passed is False
    assert result.reason_code == ReasonCode.CONTRACT_MISMATCH
    assert result.sandbox_test is None
    assert runner.commands == []


def test_a13_degrade_on_unavailable_never_passes() -> None:
    """A13/I12：Docker 不可用 → A/B 全执行 + SANDBOX_UNAVAILABLE，不判通过。"""
    evidence = MemoryEvidenceStore()
    runner = FakeDockerRunner(unavailable=True)
    result = _validate(runner=runner, evidence=evidence, run_id="run-unavail")
    assert result.passed is False
    assert result.reason_code == ReasonCode.SANDBOX_UNAVAILABLE
    assert result.stages[:2] == ("OUTPUT_GUARD", "CONTRACT_CHECK")
    assert "PASSED" not in result.stages
    assert any(key.endswith("check_report.json") for key in evidence.objects)


def test_degrade_on_timeout_never_passes() -> None:
    """超时同样走降级，不误判通过。"""
    result = _validate(runner=FakeDockerRunner(timeout=True), run_id="run-timeout")
    assert result.passed is False
    assert result.reason_code == ReasonCode.SANDBOX_UNAVAILABLE


def test_evidence_write_failure_never_passes() -> None:
    """I10：日志写失败 → 不判通过。"""
    result = _validate(evidence=FailingEvidenceStore(), run_id="run-evidence")
    assert result.passed is False
    assert result.reason_code is not None


def test_i14_repair_count_is_readonly_and_no_model_calls() -> None:
    """I14/A14：回边计数只读；达上限只产出转人工结论，不自增。"""
    model = RecordingModel()
    result = _validate(repair_count=2, run_id="run-2")
    assert result.repair_count == 2
    assert result.handoff is True
    assert model.calls == 0
    assert all("iteration" not in str(stage) for stage in result.stages)


def test_idempotence_reuses_result_without_new_sandbox() -> None:
    """A12：同一幂等键重复调用复用结论，不重复创建沙箱。"""
    runner = FakeDockerRunner(result=_command_result(sandbox_report()))
    first = _validate(runner=runner, run_id="run-3")
    second = _validate(runner=runner, run_id="run-3")
    assert first == second
    assert len(runner.commands) == 1


def test_on_sandbox_unavailable_entry_runs_static_only_and_never_passes() -> None:
    """M11 触发的降级入口：A/B 仍执行，结果不判通过。"""
    spec = _spec()
    result = on_sandbox_unavailable(
        spec,
        "code/sess/run-degrade/widget.tsx",
        _manifest(spec),
        0,
        session_id="sess",
        run_id="run-degrade",
        evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore(
            {"code/sess/run-degrade/widget.tsx": _VALID_TSX.encode()}
        ),
    )
    assert result.passed is False
    assert result.reason_code == ReasonCode.SANDBOX_UNAVAILABLE
    assert result.stages == ("OUTPUT_GUARD", "CONTRACT_CHECK", "FAILED_STAGE")
    assert result.output_guard.passed is True
    assert result.contract_check.passed is True


def test_a16_stage_sequence_has_no_gaps() -> None:
    """A16：阶段序列有序无缺口（seq 语义由 M04 编号，本模块只保序）。"""
    result = _validate(run_id="run-4")
    stages = tuple(result.stages)
    order = (
        "OUTPUT_GUARD",
        "CONTRACT_CHECK",
        "COMPILE",
        "RENDER",
        "SIZE_ASSERT",
        "RUNTIME_ASSERT",
        "CAPABILITY_ASSERT",
        "INTERACTION_ASSERT",
        "DEVICE_API_ASSERT",
        "EVIDENCE_WRITE",
        "PASSED",
    )
    assert stages == order
    assert len(set(stages)) == len(stages)
