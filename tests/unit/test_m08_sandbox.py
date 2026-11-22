"""M08 一次性沙箱单测：A6-A15 + I6-I12/I15/I17，全部走测试桩。"""

from __future__ import annotations

import hashlib
import json

import pytest

from packages.agent_core.types import JsonObject, JsonValue
from packages.contracts.enums import GridSpec, ReasonCode
from services.guardrails.output_ports import SandboxResult
from services.sandbox_runner.assertions import (
    assert_capabilities_via_bridge,
    assert_interactions,
    assert_no_runtime_error,
    assert_size,
)
from services.sandbox_runner.bridge_stub import evaluate_bridge_events
from services.sandbox_runner.entry import destroy_sandbox, run_sandbox
from services.sandbox_runner.evidence import verify_evidence_refs
from services.sandbox_runner.executor import execute_code
from services.sandbox_runner.ports import CommandResult, SandboxUnavailableError
from tests.support.m08 import (
    FakeDockerRunner,
    FakeSourceStore,
    MemoryEvidenceStore,
    sandbox_report,
)

_SRC = "export default function Widget() { return <div/>; }"
_REF = "code/sess/run-1/widget.tsx#sha256:" + hashlib.sha256(_SRC.encode()).hexdigest()
_MANIFEST = {"grid": "2x2", "capabilities": ["date"]}
_LIMITS = {"sandbox_hard_timeout_ms": 15000, "render_timeout_ms": 5000}


def _run(
    runner: FakeDockerRunner,
    *,
    evidence: MemoryEvidenceStore | None = None,
    source_ref: str = _REF,
    manifest: JsonObject | None = None,
) -> SandboxResult:
    evidence = evidence or MemoryEvidenceStore()
    return run_sandbox(
        run_id="run-1",
        source_ref=source_ref,
        manifest=manifest or _MANIFEST,
        limits=_LIMITS,
        source_store=FakeSourceStore({source_ref: _SRC.encode()}),
        evidence_store=evidence,
        docker_runner=runner,
        prefix="evidence/sess/run-1",
    )


def _success_runner() -> FakeDockerRunner:
    return FakeDockerRunner(result=_command_result(sandbox_report()))


def _command_result(value: JsonValue) -> CommandResult:
    return CommandResult(stdout=json.dumps(value), stderr="", exit_code=0)


def test_a6_compile_failure_is_reported_with_evidence() -> None:
    """A6：编译失败 → COMPILE_FAILED + 日志带 hash，绝不判通过。"""
    evidence = MemoryEvidenceStore()
    report = sandbox_report(compiled=None, logs="compile error\n")
    result = _run(FakeDockerRunner(result=_command_result(report)), evidence=evidence)
    assert result.passed is False
    assert result.reason_code == ReasonCode.COMPILE_FAILED
    assert any(c.name == "compile" and not c.passed for c in result.cases)
    assert result.log_ref.split("#")[0] in evidence.objects
    assert evidence.get(result.log_ref) == b"compile error\n"


def test_a7_size_assertion_blocks_oversized_widget() -> None:
    """A7：2x1 塞入 480px 月视图 → SANDBOX_TEST_FAILED + 快照。"""
    report = sandbox_report(
        render={
            "grid": "2x1",
            "width_px": 480,
            "height_px": 80,
            "max_child_width_px": 480,
            "max_child_height_px": 80,
            "errors": [],
        }
    )
    result = _run(
        FakeDockerRunner(result=_command_result(report)),
        manifest={"grid": "2x1", "capabilities": ["date"]},
    )
    assert result.passed is False
    assert result.reason_code == ReasonCode.SANDBOX_TEST_FAILED
    assert any(c.name == "size_assert" and not c.passed for c in result.cases)
    assert result.snapshot_ref is not None


def test_a8_undeclared_capability_denied_and_reported() -> None:
    """A8/I8：未声明 timer 调用被 bridge 拒绝并上报 widget:error。"""
    report = sandbox_report(
        bridge_events=[
            {"type": "capability_request", "capability": "timer", "allowed": False},
            {"type": "widget:error", "code": "CAPABILITY_DENIED"},
        ]
    )
    result = _run(FakeDockerRunner(result=_command_result(report)))
    assert result.passed is False
    assert result.reason_code == ReasonCode.CAPABILITY_DENIED
    assert any(c.name == "capability_via_bridge" and not c.passed for c in result.cases)


def test_a9_four_interaction_cases_are_all_recorded() -> None:
    """A9/I9：四类交互逐条留 cases[]。"""
    result = _run(_success_runner())
    names = [c.name for c in result.cases]
    for name in ("calendar_next_month", "clock_tick", "alarm_toggle", "task_check"):
        assert name in names
        assert isinstance(result.cases[names.index(name)].passed, bool)


def test_a10_runtime_error_fails() -> None:
    """A10：渲染期未捕获异常 → runtime_no_error 失败。"""
    report = sandbox_report(
        render={
            "grid": "2x2",
            "width_px": 160,
            "height_px": 160,
            "max_child_width_px": 150,
            "max_child_height_px": 150,
            "errors": ["TypeError: boom"],
        }
    )
    result = _run(FakeDockerRunner(result=_command_result(report)))
    assert result.passed is False
    assert any(c.name == "runtime_no_error" and not c.passed for c in result.cases)


def test_a11_timeout_kills_container_and_leaves_no_residue() -> None:
    """A11/I7：超时强制销毁，容器列表为空。"""
    runner = FakeDockerRunner(timeout=True)
    with pytest.raises(SandboxUnavailableError):
        _run(runner)
    assert runner.killed and all(not runner.exists(name) for name in runner.killed)


def test_a12_same_compiled_hash_is_deterministic() -> None:
    """A12/I13：同一 compiled.hash 两次结果逐字段一致。"""
    first = _run(_success_runner())
    second = _run(_success_runner())
    assert first == second


def test_i6_docker_command_has_full_isolation_flags() -> None:
    """I6：一次性容器带真隔离参数，不挂 docker.sock、不共享控制面卷。"""
    runner = _success_runner()
    _run(runner)
    cmd = " ".join(runner.commands[0])
    for flag in (
        "--rm",
        "--network none",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--security-opt seccomp=",
        "--user 10001:10001",
        "--memory 512m",
        "--cpus 1",
        "--pids-limit 64",
        "--read-only",
        "--tmpfs /tmp:rw,noexec,nosuid,size=64m",
    ):
        assert flag in cmd
    assert "docker.sock" not in cmd
    assert "-v" not in cmd and "--volume" not in cmd
    assert runner.timeouts == [15]


def test_i10_evidence_refs_verified_before_passed() -> None:
    """I10：证据缺失/被替换不判 PASSED。"""
    evidence = MemoryEvidenceStore()
    result = _run(_success_runner(), evidence=evidence)
    assert result.passed is True
    assert verify_evidence_refs(evidence, result.evidence_refs) is True
    evidence.tamper(result.log_ref.split("#")[0], b"tampered")
    assert verify_evidence_refs(evidence, result.evidence_refs) is False


def test_i11_failure_carries_reason_and_evidence_ref() -> None:
    """I11：失败必带 ReasonCode + evidenceRef + 可读证据。"""
    evidence = MemoryEvidenceStore()
    result = _run(
        FakeDockerRunner(result=_command_result(sandbox_report(compiled=None))),
        evidence=evidence,
    )
    assert result.passed is False
    assert result.reason_code is not None
    assert result.evidence_ref
    assert evidence.get(result.log_ref)


def test_i15_destroy_sandbox_is_idempotent() -> None:
    """A15：销毁幂等，成功/失败路径都不留残留。"""
    runner = FakeDockerRunner(result=_command_result(sandbox_report()))
    _run(runner)
    destroy_sandbox("run-1", "success", runner)
    destroy_sandbox("run-1", "success", runner)
    assert all(not runner.exists(name) for name in runner.killed)


def test_assert_size_uses_cell_80px() -> None:
    """R9：cell=80px 的尺寸映射。"""
    assert assert_size(GridSpec._2X1, {"width_px": 160, "height_px": 80, "max_child_width_px": 150, "max_child_height_px": 80}) is True
    assert assert_size(GridSpec._2X1, {"width_px": 480, "height_px": 80, "max_child_width_px": 480, "max_child_height_px": 80}) is False


def test_assert_no_runtime_error_and_bridge_pure_helpers() -> None:
    """R10/R11 纯函数断言。"""
    assert assert_no_runtime_error([]) is True
    assert assert_no_runtime_error(["boom"]) is False
    verdict = evaluate_bridge_events(
        [{"type": "capability_request", "capability": "date", "allowed": True}],
        frozenset({"date"}),
    )
    assert verdict.passed is True
    assert assert_capabilities_via_bridge(
        [{"type": "capability_request", "capability": "date", "allowed": True}],
        {"date"},
    ) is True


def test_assert_interactions_only_checks_applicable_cases() -> None:
    """去类型化：不适用交互不误判失败，适用交互失败要拦。"""
    cases = [
        {"name": "calendar_next_month", "passed": True, "applicable": False},
        {"name": "task_check", "passed": False, "applicable": True},
    ]
    assert assert_interactions("custom", cases) is False


def test_execute_code_kept_compatibility_with_hardened_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """兼容旧 execute_code：命令升级为真隔离，保留 --rm 与镜像。"""
    calls: list[list[str]] = []
    import subprocess

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr("services.sandbox_runner.executor.subprocess.run", fake_run)
    result = execute_code("print(1)", timeout_s=5)
    assert result.ok is True
    cmd = " ".join(calls[0])
    assert "python:3.13-slim" in cmd
    assert "--network none" in cmd
    assert "--cap-drop ALL" in cmd
    assert "--user 10001:10001" in cmd
