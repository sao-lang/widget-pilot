"""M08 → M09 产物适配器红灯测试（A1/A2/I1）。

M08 真实产出 ValidateResult{output_guard,contract_check,sandbox_test,compiled_ref...}，
M09 需要把它折叠成 WidgetBundle 的 static_check/sandbox_test/compiled，
不重测、不改 M08 结论。失败原因=缺适配器实现。
"""
from __future__ import annotations

import pytest

from packages.contracts.enums import ReasonCode
from services.control_plane.bundles.adapter import (
    adapt_validation_to_bundle,
    is_validation_passed,
)
from services.control_plane.bundles.errors import BundleEvidenceError
from services.guardrails.output_ports import (
    ContractCheckReport,
    EvidenceRefs,
    GuardJudge,
    OutputGuardReport,
    SandboxCase,
    SandboxResult,
    ValidateResult,
    Violation,
)


def _refs() -> EvidenceRefs:
    return EvidenceRefs(
        log_ref="evidence/s/r/sandbox.log#sha256:" + "a" * 64,
        report_ref="evidence/s/r/report.json#sha256:" + "b" * 64,
        snapshot_ref="evidence/s/r/snapshot.html#sha256:" + "c" * 64,
        case_refs=(),
        compiled_ref="code/s/r/compiled.js",
        compiled_hash="d" * 64,
    )


def _validate(*, passed: bool, sb_passed: bool = True) -> ValidateResult:
    return ValidateResult(
        passed=passed,
        output_guard=OutputGuardReport(
            passed=passed,
            hit=not passed,
            judge=GuardJudge.SCHEMA if not passed else None,
            violations=(Violation("Call", "1:0", "e" * 64, "forbidden_eval"),)
            if not passed
            else (),
            reason_code=ReasonCode.GUARD_OUTPUT_HIT if not passed else None,
            evidence_ref="evidence/s/r/guard#sha256:" + "f" * 64,
        ),
        contract_check=ContractCheckReport(
            passed=passed,
            violations=() if passed else ("capability_mismatch",),
            spec_hash="1" * 64,
            evidence_ref="evidence/s/r/contract#sha256:" + "2" * 64,
        ),
        sandbox_test=SandboxResult(
            passed=sb_passed,
            cases=(SandboxCase("render", sb_passed, "case#sha256:" + "3" * 64),),
            log_ref=_refs().log_ref,
            snapshot_ref=_refs().snapshot_ref,
            compiled_hash="d" * 64,
            reason_code=None if sb_passed else ReasonCode.SANDBOX_TEST_FAILED,
            evidence_ref="evidence/s/r/sandbox#sha256:" + "4" * 64,
            evidence_refs=_refs(),
        ),
        reason_code=None if passed else ReasonCode.GUARD_OUTPUT_HIT,
        evidence_ref="evidence/s/r/all#sha256:" + "5" * 64,
        stages=("guard", "contract", "sandbox"),
        repair_count=0,
    )


def test_is_validation_passed_requires_all_gates() -> None:
    """三道门禁全过才允许成包（I1：证据先于结论）。"""
    assert is_validation_passed(_validate(passed=True)) is True
    assert is_validation_passed(_validate(passed=False)) is False
    assert is_validation_passed(_validate(passed=True, sb_passed=False)) is False


def test_adapt_folds_guard_and_contract_into_static_check() -> None:
    """output_guard + contract_check 折叠成 static_check，不改结论。"""
    bundle = adapt_validation_to_bundle(
        validation=_validate(passed=True),
        bundle_id="b1",
        user_id="u1",
        session_id="s1",
        spec={},
    )
    static_check = bundle["static_check"]
    sandbox_test = bundle["sandbox_test"]
    compiled = bundle["compiled"]
    assert isinstance(static_check, dict) and static_check["passed"] is True
    assert isinstance(static_check, dict) and static_check["violations"] == []
    assert isinstance(sandbox_test, dict) and sandbox_test["passed"] is True
    assert isinstance(compiled, dict) and compiled["hash"] == "d" * 64


def test_adapt_rejects_failed_validation() -> None:
    """未通过校验不得成包（A2）。"""
    with pytest.raises(BundleEvidenceError):
        adapt_validation_to_bundle(
            validation=_validate(passed=False),
            bundle_id="b1",
            user_id="u1",
            session_id="s1",
            spec={},
        )


def test_adapt_preserves_violations_on_guard_hit() -> None:
    """护栏命中时违规清单折叠进 static_check.violations（不改 M08 结论）。"""
    validation = _validate(passed=True)
    # 构造 output_guard 命中但 total passed=True 的不一致态，适配器应保守拒绝
    from dataclasses import replace

    inconsistent = replace(
        validation,
        output_guard=replace(validation.output_guard, passed=False, hit=True),
    )
    with pytest.raises(BundleEvidenceError):
        adapt_validation_to_bundle(
            validation=inconsistent,
            bundle_id="b1",
            user_id="u1",
            session_id="s1",
            spec={},
        )
