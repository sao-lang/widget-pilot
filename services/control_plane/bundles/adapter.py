"""M08 → M09 产物适配器（FR-9 / I1 / A1/A2）。

M08 真实产出 ``ValidateResult{output_guard, contract_check, sandbox_test,
evidence_refs{compiled_ref, compiled_hash,...}}``；M09 成包需要
``static_check{passed,violations}`` + ``sandbox_test{passed,cases,log_ref,snapshot_ref}``
+ ``compiled{content_ref,hash}``。本适配器做**视图折叠**，不改 M08 结论、不重测、
不产代码。任一门禁未过或出现不一致态（total passed 但子门禁 failed）一律保守拒绝。
"""
from __future__ import annotations

from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.bundles.errors import BundleEvidenceError
from services.guardrails.output_ports import ValidateResult, parse_object_ref


def is_validation_passed(validation: ValidateResult) -> bool:
    """三道门禁全过才允许成包（I1 证据先于结论）。

    保守判定：total passed 且 output_guard/contract_check/sandbox_test 各自通过，
    且三者无不一致态。缺 sandbox_test（如降级路径）视为未通过。
    """
    sandbox = validation.sandbox_test
    return (
        validation.passed
        and validation.output_guard.passed
        and not validation.output_guard.hit
        and validation.contract_check.passed
        and sandbox is not None
        and sandbox.passed
    )


def adapt_validation_to_bundle(
    *,
    validation: ValidateResult,
    bundle_id: str,
    user_id: str,
    session_id: str,
    spec: JsonObject,
) -> JsonObject:
    """把 ValidateResult 折叠成 WidgetBundle 的证据字段（A1）。

    Args:
        validation: M08 汇总结论（生产由编排注入，测试由 tests/support 构造）。
        bundle_id: 目标包标识。
        user_id: 租户隔离键。
        session_id: 所属会话。
        spec: WidgetSpec（M07 产出，只读纳入包）。

    Returns:
        含 success 证据的 bundle 形态 dict（hash/state/versions 由装配层补）。

    Raises:
        BundleEvidenceError: 校验未过 / 不一致态 / 编译引用非法（fail-closed）。
    """
    if not is_validation_passed(validation):
        raise BundleEvidenceError()
    sandbox = validation.sandbox_test
    if sandbox is None:  # is_validation_passed 已覆盖，此处为类型收窄
        raise BundleEvidenceError()

    # 折叠 static_check：guard violations + contract violations，不改结论。
    violations: list[JsonValue] = [
        f"{v.node_kind}:{v.rule}" for v in validation.output_guard.violations
    ]
    violations.extend(validation.contract_check.violations)

    # 折叠 sandbox_test：cases 转 {name,passed,detail_ref}（schema 形状）。
    cases: list[JsonValue] = [
        {"name": c.name, "passed": c.passed, "detail_ref": c.detail_ref}
        for c in sandbox.cases
    ]

    # 编译引用：EvidenceRefs 挂在 sandbox_test.evidence_refs 上（M08 ValidateResult
    # 顶层只有 evidence_ref 汇总串）；compiled_ref 是 key，compiled_hash 单独字段。
    # 缺失或 hash 非法 fail-closed。
    refs = sandbox.evidence_refs
    compiled_hash = refs.compiled_hash
    if len(compiled_hash) != 64:
        raise BundleEvidenceError("编译产物 hash 非法，拒绝成包")
    compiled_ref = refs.compiled_ref
    try:
        # 若 M08 未来改为带 hash 后缀的引用，拆出纯 key 纳入包。
        compiled_ref, _embedded = parse_object_ref(compiled_ref)
    except ValueError:
        # 当前 M08 compiled_ref 为纯 key，parse 失败属正常，保留原值。
        pass
    if not compiled_ref:
        raise BundleEvidenceError("编译产物引用为空，拒绝成包")

    return {
        "bundle_id": bundle_id,
        "user_id": user_id,
        "session_id": session_id,
        "spec": spec,
        "source": {"path": "widget.tsx", "content_ref": "", "language": "tsx"},
        "compiled": {"content_ref": compiled_ref, "hash": compiled_hash},
        "manifest": {
            "spec_hash": validation.contract_check.spec_hash,
        },
        "static_check": {
            "passed": True,
            "violations": violations,
        },
        "sandbox_test": {
            "passed": True,
            "cases": cases,
            "log_ref": sandbox.evidence_refs.log_ref,
            "snapshot_ref": sandbox.evidence_refs.snapshot_ref,
        },
        "evidence_ref": validation.evidence_ref,
    }
