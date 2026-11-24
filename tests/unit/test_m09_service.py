"""M09 BundleService 接线 RED 测试（成包/状态机/回滚/证据/隔离/联动）。

断言上游 M08 结论被 BundleService 真实消费、artifact 事件真实发出、
M07/M10 联动真实调用。失败原因=缺接线实现。
"""
from __future__ import annotations

from typing import cast

import pytest

from packages.contracts.enums import BundleState
from services.control_plane.bundles.errors import (
    BundleConflictError,
    BundleForbiddenError,
)
from services.control_plane.bundles.service import BundleService
from services.guardrails.output_ports import (
    ContractCheckReport,
    EvidenceRefs,
    OutputGuardReport,
    SandboxCase,
    SandboxResult,
    ValidateResult,
)
from tests.support.bundles import (
    InMemoryBundleStore,
    RecordingArtifactAdoption,
    RecordingCorpusLink,
    RecordingEventSink,
    RecordingPreferenceRevocation,
)
from tests.support.control_plane import AuditLedger


def _refs() -> EvidenceRefs:
    return EvidenceRefs(
        log_ref="evidence/s/r/sandbox.log#sha256:" + "a" * 64,
        report_ref="evidence/s/r/report.json#sha256:" + "b" * 64,
        snapshot_ref=None,
        case_refs=(),
        compiled_ref="code/s/r/compiled.js",
        compiled_hash="d" * 64,
    )


def _validation() -> ValidateResult:
    refs = _refs()
    return ValidateResult(
        passed=True,
        output_guard=OutputGuardReport(
            passed=True, hit=False, judge=None, violations=(),
            reason_code=None, evidence_ref="guard#sha256:" + "e" * 64,
        ),
        contract_check=ContractCheckReport(
            passed=True, violations=(), spec_hash="1" * 64,
            evidence_ref="contract#sha256:" + "2" * 64,
        ),
        sandbox_test=SandboxResult(
            passed=True, cases=(SandboxCase("render", True, "case#sha256:" + "3" * 64),),
            log_ref=refs.log_ref, snapshot_ref=None, compiled_hash="d" * 64,
            reason_code=None, evidence_ref="sandbox#sha256:" + "4" * 64,
            evidence_refs=refs,
        ),
        reason_code=None, evidence_ref="all#sha256:" + "5" * 64,
        stages=("guard", "contract", "sandbox"), repair_count=0,
    )


def _spec() -> dict:
    return {
        "spec_version": "v1", "grid": "2x2", "widget_type": "clock",
        "capabilities": ["date", "theme"], "import_allowlist": ["react", "@widget/bridge"],
        "props_contract": ["grid", "theme", "tick"], "model": "mock",
        "prompt_version": "v1", "model_tier": "cheap",
    }


def _service() -> tuple[BundleService, RecordingEventSink, RecordingCorpusLink, RecordingPreferenceRevocation, AuditLedger, RecordingArtifactAdoption]:
    store = InMemoryBundleStore()
    events = RecordingEventSink()
    corpus = RecordingCorpusLink()
    prefs = RecordingPreferenceRevocation()
    adoption = RecordingArtifactAdoption()
    audit = AuditLedger()
    svc = BundleService(
        store=store, audit=audit, events=events, corpus=corpus,
        preference_revocation=prefs, adoption=adoption,
        confirm_secret="test-confirm-secret",
    )
    return svc, events, corpus, prefs, audit, adoption


def test_assemble_consumes_m08_result_and_emits_artifact() -> None:
    """成包真实消费 M08 结论并发出 artifact 事件（A1 / 接线）。"""
    svc, events, _corpus, _prefs, audit, _adoption = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="code/s/r/widget.tsx#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    assert bundle["state"] == BundleState.GENERATED.value
    assert isinstance(bundle["hash"], str) and len(bundle["hash"]) == 64
    assert isinstance(bundle["versions"], list) and len(bundle["versions"]) == 1
    assert "artifact" in events.kinds()
    assert events.events[0][1]["bundle_id"] == bundle["bundle_id"]
    assert len(audit.all()) == 1


def test_install_is_idempotent_with_single_audit() -> None:
    """重复 install 只有一次审计副作用（A12）。"""
    svc, _events, _corpus, _prefs, audit, _adoption = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    svc.install(bid, "u1")
    svc.install(bid, "u1")
    assert svc.get(bid, "u1")["state"] == BundleState.INSTALLED.value
    install_audits = [a for a in audit.all() if a.action == "bundle_install"]
    assert len(install_audits) == 1


def test_delete_requires_confirm_and_link_revocation() -> None:
    """删除二次确认 + M07 撤销 + M10 联动真实调用（A13 / 接线）。"""
    svc, _events, corpus, prefs, _audit, _adoption = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    from services.control_plane.bundles.errors import ConfirmRequiredError

    with pytest.raises(ConfirmRequiredError):
        svc.delete(bid, "u1", None)
    # B1：伪造/未签名 token 一律拒绝（真 HMAC 校验，非「非空即放行」）。
    with pytest.raises(ConfirmRequiredError):
        svc.delete(bid, "u1", "tok", trace_id="t1")
    token, _expires = svc.issue_delete_confirm(bid, "u1")
    result = svc.delete(bid, "u1", token, trace_id="t1")
    assert result["state"] == BundleState.DELETED.value
    assert corpus.deleted == [bid]
    assert prefs.revoked == [("u1", bid, "t1")]
    with pytest.raises(BundleConflictError):
        svc.install(bid, "u1")


def test_token_is_bound_to_user_and_bundle() -> None:
    """B1：confirm_token 绑定 user+bundle，跨用户/跨包复用被拒（A13）。"""
    from services.control_plane.bundles.errors import ConfirmRequiredError

    svc, *_ = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    token, _ = svc.issue_delete_confirm(bid, "u1")
    # 跨用户复用：u2 拿 u1 的 token 删除 → 拒绝（越权阶段先拦或 token 错配）。
    with pytest.raises((ConfirmRequiredError, BundleForbiddenError)):
        svc.delete(bid, "u2", token)


def test_install_emits_adoption_signal_once() -> None:
    """B3：安装产物发 adopted_artifact 信号（携带 palette），重复安装不重复发。"""
    svc, _events, _corpus, _prefs, _audit, adoption = _service()
    spec = _spec()
    spec["style"] = {"palette": ["warm", "amber"]}
    bundle = svc.assemble(
        validation=_validation(), spec=spec, source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    svc.install(bid, "u1")
    svc.install(bid, "u1")  # 幂等：不重复发信号
    assert len(adoption.adopted) == 1
    assert adoption.adopted[0][0] == "u1"
    assert adoption.adopted[0][1] == bid
    assert adoption.adopted[0][2] == "warm"


def test_rollback_reuses_previous_compiled_ref() -> None:
    """回滚沿用上一版编译产物（A15 / 接线）。"""
    svc, _events, _corpus, prefs, _audit, _adoption = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    # 手动追加第二版后再回滚
    store_store = cast(InMemoryBundleStore, svc._store)  # 测试内访问注入桩验证版本链
    versions = store_store.versions[bid]
    from services.control_plane.bundles.versions import append_version
    versions2 = append_version(versions, "code/v2.js", "h2", "m2", bundle_id=bid)
    store_store.replace_versions(bid, "u1", versions2)
    result = svc.rollback(bid, "u1", None, trace_id="t2")
    result_versions = result["versions"]
    assert isinstance(result_versions, list) and len(result_versions) == 3
    last = result_versions[-1]
    assert isinstance(last, dict) and last["compiled_ref"] == versions[0].compiled_ref
    assert prefs.revoked


def test_evidence_has_five_categories() -> None:
    """evidence 接口返回五类齐全（A3 / I14）。"""
    svc, *_ = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    evidence = svc.evidence(str(bundle["bundle_id"]), "u1")
    for key in ("spec", "manifest", "static_check", "sandbox_test", "trace"):
        assert key in evidence
    static_check = evidence["static_check"]
    assert isinstance(static_check, dict) and static_check["passed"] is True


def test_cross_tenant_access_is_forbidden() -> None:
    """越权访问他人包 403（A16 / I15）。"""
    svc, *_ = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    with pytest.raises(BundleForbiddenError):
        svc.get(bid, "u2")
    with pytest.raises(BundleForbiddenError):
        svc.preview(bid, "u2")


def test_malformed_confirm_token_is_400_not_500() -> None:
    """review 修复：畸形（非 ASCII/格式非法）token 折叠为 ConfirmRequiredError（400），非冒泡 500。"""
    from services.control_plane.bundles.errors import ConfirmRequiredError

    svc, *_ = _service()
    bundle = svc.assemble(
        validation=_validation(), spec=_spec(), source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    for bad in ("é.abc", "xxx.é", "no-separator", "éé"):
        with pytest.raises(ConfirmRequiredError):
            svc.delete(bid, "u1", bad)
