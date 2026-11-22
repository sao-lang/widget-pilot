"""M08 沙箱入口与汇总门禁（阶段 C + A→B→C 串联）。

一次性容器：宿主机直调 Docker，--network none / --cap-drop ALL / 非 root /
cgroups 限额 / seccomp / 15s 硬超时强杀；不挂 docker.sock 给容器内 runner。
汇总入口只产出结论（ReasonCode + evidenceRef），回边调度归 M07/M03。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from packages.contracts.enums import MAX_REPAIR, ReasonCode
from packages.contracts.types import JsonValue
from services.guardrails.capability import declared_capabilities
from services.guardrails.contract_check import check_contract, compute_spec_hash
from services.guardrails.output_guard import check_output_guard
from services.guardrails.output_ports import (
    ContractCheckReport,
    EvidenceRefs,
    OutputGuardReport,
    SandboxCase,
    SandboxResult,
    SourceReaderPort,
    ValidateResult,
    canonical_json,
    sha256_hex,
)
from services.guardrails.static_scan import parse_tsx
from services.sandbox_runner.assertions import (
    assert_capabilities_via_bridge,
    assert_interactions,
    assert_no_runtime_error,
    assert_size,
)
from services.sandbox_runner.compile import compile_tsx
from services.sandbox_runner.evidence import verify_evidence_refs, write_evidence
from services.sandbox_runner.ports import (
    DockerCliRunner,
    DockerRunnerPort,
    EvidenceStorePort,
    SandboxUnavailableError,
)
from services.sandbox_runner.render import parse_render_stage
from services.sandbox_runner.snapshot import (
    ChromeSnapshotRenderer,
    SnapshotRendererPort,
)

__all__ = [
    "SANDBOX_HARD_TIMEOUT_S",
    "SANDBOX_IMAGE",
    "destroy_sandbox",
    "on_sandbox_unavailable",
    "run_sandbox",
    "validate_and_test",
]

SANDBOX_IMAGE = "wp-sandbox:dev"
SANDBOX_HARD_TIMEOUT_S = 15
_DEFAULT_LIMITS: dict[str, int] = {"sandbox_hard_timeout_ms": 15000, "render_timeout_ms": 5000}
_SECCONFIG_PATH = Path(__file__).resolve().parents[2] / "infra/sandbox/seccomp.json"
_RESULT_CACHE: dict[tuple[str, ...], ValidateResult] = {}
_CACHE_LOCK = threading.Lock()


logger = logging.getLogger(__name__)
"""快照 png 渲染失败告警（只丢像素证据，不阻断主校验）。"""


def _container_name(run_id: str) -> str:
    """容器名只读 run_id 派生：同 run 同时最多一个沙箱，且可精确强杀。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", run_id)[:48] or "run"
    return f"wp-sbx-{safe}"


def _docker_command(run_id: str, image: str) -> list[str]:
    """真隔离命令：无网络、无 capability、非 root、cgroups/seccomp 限额、只读根。"""
    return [
        "docker", "run", "--rm", "-i", "--name", _container_name(run_id),
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--security-opt", f"seccomp={_SECCONFIG_PATH.as_posix()}",
        "--user", "10001:10001",
        "--memory", "512m",
        "--cpus", "1",
        "--pids-limit", "64",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        image,
    ]


def destroy_sandbox(run_id: str, reason: str, docker_runner: DockerRunnerPort) -> None:
    """成功/失败/超时/取消四条路径都调用；强制销毁且不复用实例。"""
    del reason
    docker_runner.kill(_container_name(run_id))


def run_sandbox(
    run_id: str,
    source_ref: str,
    manifest: Mapping[str, JsonValue],
    limits: Mapping[str, JsonValue],
    *,
    source_store: SourceReaderPort,
    evidence_store: EvidenceStorePort,
    docker_runner: DockerRunnerPort,
    image: str = SANDBOX_IMAGE,
    prefix: str = "",
    snapshot_renderer: SnapshotRendererPort | None = None,
) -> SandboxResult:
    """一次性沙箱：固定顺序 编译→渲染→尺寸→运行时→能力→交互→证据。

    snapshot_renderer（R8/R13）：把沙箱 DOM 序列化 html 交由宿主机 headless
    Chrome 渲染真像素 png；缺渲染器时只有 html 结构证据、png_ref=None（降级，
    不阻断校验）。渲染在编排进程只处理已序列化 DOM，不执行 widget 逻辑。
    """
    evidence_prefix = prefix or f"evidence/{run_id}"
    hard_timeout = _hard_timeout_s(limits)
    render_timeout = _as_int(limits.get("render_timeout_ms", _DEFAULT_LIMITS["render_timeout_ms"]))
    grid = str(manifest.get("grid", ""))
    declared = declared_capabilities(manifest)
    stages: list[str] = []
    case_specs: list[tuple[str, bool]] = []
    reason: ReasonCode | None = None
    compiled_ref = ""
    compiled_hash = ""
    log = b""
    snapshot: bytes | None = None
    report: dict[str, JsonValue] = {}

    try:
        source = source_store.get(source_ref).decode("utf-8")
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        raise SandboxUnavailableError("source reference unreadable") from exc

    payload = {
        "source": source,
        "manifest": {"grid": grid, "capabilities": sorted(declared)},
        "limits": {"render_timeout_ms": max(1, render_timeout)},
    }
    payload_bytes = canonical_json(payload).encode("utf-8")

    try:
        command = _docker_command(run_id, image)
        result = docker_runner.run(command, stdin=payload_bytes, timeout_s=hard_timeout)
        parsed = json.loads(result.stdout) if result.stdout.strip() else {}
        report = _as_object(parsed)
        if not report:
            raise SandboxUnavailableError("sandbox runner returned empty report")

        stages.append("COMPILE")
        compiled = _as_object(report.get("stages", {})).get("compile", {})
        compiled_source = _as_object(compiled).get("compiled")
        if isinstance(compiled_source, str) and compiled_source:
            artifact = compile_tsx(evidence_prefix, compiled_source, evidence_store)
            compiled_ref, compiled_hash = artifact.ref, artifact.hash
            case_specs.append(("compile", True))
        else:
            reason = ReasonCode.COMPILE_FAILED
            case_specs.append(("compile", False))

        if reason is None:
            stages.append("RENDER")
            render_stage = _as_object(report.get("stages", {})).get("render", {})
            metrics = parse_render_stage(_as_object(render_stage), grid)
            case_specs.append(("render", True))

            stages.append("SIZE_ASSERT")
            size_ok = assert_size(grid, metrics)
            case_specs.append(("size_assert", size_ok))

            stages.append("RUNTIME_ASSERT")
            runtime_ok = assert_no_runtime_error(metrics.errors)
            case_specs.append(("runtime_no_error", runtime_ok))

            stages.append("CAPABILITY_ASSERT")
            bridge_stage = _as_object(report.get("stages", {})).get("bridge", {})
            events = _as_object(bridge_stage).get("events", ())
            event_list = [item for item in events if isinstance(item, dict)] if isinstance(events, (list, tuple)) else []
            capability_ok = assert_capabilities_via_bridge(event_list, set(declared))
            case_specs.append(("capability_via_bridge", capability_ok))

            stages.append("INTERACTION_ASSERT")
            interactions = report.get("interactions", ())
            interaction_list = [
                item for item in interactions if isinstance(item, dict)
            ] if isinstance(interactions, (list, tuple)) else []
            interactions_ok = assert_interactions("custom", interaction_list)
            for item in interaction_list:
                name = str(item.get("name", "unknown"))
                case_specs.append((name, item.get("passed") is True))

            if not capability_ok:
                reason = ReasonCode.CAPABILITY_DENIED
            elif not (size_ok and runtime_ok and interactions_ok):
                reason = ReasonCode.SANDBOX_TEST_FAILED

        log = str(report.get("logs", "")).encode("utf-8")
        snapshot_value = report.get("snapshot")
        snapshot = snapshot_value.encode("utf-8") if isinstance(snapshot_value, str) else None
    except (SandboxUnavailableError, ValueError) as exc:
        # 基础设施失败/报告不可解析：写降级日志后上抛，交 validate_and_test 走降级路径。
        log = f"sandbox unavailable: {exc}".encode()
        try:
            write_evidence(
                evidence_prefix, {"error": str(exc)}, log, None,
                [("sandbox_unavailable", False)], "", "", evidence_store,
            )
        except (OSError, ValueError):
            pass
        raise SandboxUnavailableError(str(exc)) from exc
    finally:
        destroy_sandbox(run_id, reason.value if reason else "unavailable", docker_runner)

    # R8/R13：DOM 序列化 html → 宿主机 headless Chrome 真像素 png（可选增强）。
    # 只静态渲染已序列化 DOM，不执行 widget 逻辑；失败返回 None 不阻断。
    snapshot_png: bytes | None = None
    if snapshot_renderer is not None and snapshot is not None:
        try:
            parts = grid.split("x")
            w = int(parts[0]) * 80 if len(parts) == 2 and parts[0].isdigit() else 320
            h = int(parts[1]) * 80 if len(parts) == 2 and parts[1].isdigit() else 320
            snapshot_png = snapshot_renderer.render(snapshot.decode("utf-8"), w, h)
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            logger.warning("snapshot png render failed; html evidence kept: %s", exc)
            snapshot_png = None

    stages.append("EVIDENCE_WRITE")
    try:
        refs = write_evidence(
            evidence_prefix, report, log, snapshot, case_specs,
            compiled_ref, compiled_hash, evidence_store, snapshot_png,
        )
        evidence_ok = verify_evidence_refs(evidence_store, refs)
    except (OSError, ValueError):
        refs = EvidenceRefs("", "", None, (), "", "")
        evidence_ok = False

    if not evidence_ok and reason is None:
        reason = ReasonCode.SANDBOX_TEST_FAILED
    passed = reason is None and evidence_ok
    stages.append("PASSED" if passed else "FAILED_STAGE")

    cases = tuple(
        SandboxCase(name, ok, refs.case_refs[index] if index < len(refs.case_refs) else f"{evidence_prefix}/cases/{name}.json")
        for index, (name, ok) in enumerate(case_specs)
    )
    return SandboxResult(
        passed=passed,
        cases=cases,
        log_ref=refs.log_ref,
        snapshot_ref=refs.snapshot_ref,
        compiled_hash=compiled_hash,
        reason_code=reason,
        evidence_ref=refs.report_ref or f"sandbox:{sha256_hex(log)}",
        evidence_refs=refs,
        stages=tuple(stages),
    )


def validate_and_test(
    spec: Mapping[str, JsonValue],
    source_ref: str,
    manifest_draft: Mapping[str, JsonValue],
    repair_count: int,
    *,
    session_id: str,
    run_id: str,
    evidence_store: EvidenceStorePort,
    source_store: SourceReaderPort | None = None,
    docker_runner: DockerRunnerPort | None = None,
    image: str = SANDBOX_IMAGE,
    limits: Mapping[str, JsonValue] | None = None,
    prefix: str = "",
    snapshot_renderer: SnapshotRendererPort | None = None,
) -> ValidateResult:
    """A→B→C 汇总；任一失败短路，沙箱不可用走降级且绝不判通过。

    snapshot_renderer 未注入时用真实 ChromeSnapshotRenderer（本机/部署有 Chrome
    即出真像素 png；无则 render() 返回 None 降级，不阻断校验）。
    """
    started = time.monotonic()
    key = (
        session_id, run_id, compute_spec_hash(spec), source_ref,
        sha256_hex(canonical_json(manifest_draft).encode("utf-8")), str(repair_count),
    )
    with _CACHE_LOCK:
        cached = _RESULT_CACHE.get(key)
    if cached is not None:
        return cached

    guard = check_output_guard(spec, source_ref, manifest_draft, source_store=source_store)
    if guard.hit:
        return _finalize(
            key, False, guard, None, None, guard.reason_code, guard.evidence_ref,
            ("OUTPUT_GUARD", "FAILED_STAGE"), repair_count, started,
        )

    source_store = source_store or _default_source_store()
    try:
        code_ast = parse_tsx(source_store.get(source_ref).decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        raise SandboxUnavailableError("source reference unreadable") from exc
    contract = check_contract(spec, manifest_draft, code_ast)
    if not contract.passed:
        return _finalize(
            key, False, guard, contract, None, contract.reason_code, contract.evidence_ref,
            ("OUTPUT_GUARD", "CONTRACT_CHECK", "FAILED_STAGE"), repair_count, started,
        )

    sandbox = None
    stages = ["OUTPUT_GUARD", "CONTRACT_CHECK"]
    try:
        sandbox = run_sandbox(
            run_id, source_ref, manifest_draft, limits or _DEFAULT_LIMITS,
            source_store=source_store,
            evidence_store=evidence_store,
            docker_runner=docker_runner or DockerCliRunner(),
            image=image,
            prefix=prefix or f"evidence/{session_id}/{run_id}",
            snapshot_renderer=snapshot_renderer or ChromeSnapshotRenderer(),
        )
        stages.extend(sandbox.stages)
        passed = sandbox.passed
        reason = sandbox.reason_code
        evidence_ref = sandbox.evidence_ref
    except SandboxUnavailableError as exc:
        passed = False
        reason = ReasonCode.SANDBOX_UNAVAILABLE
        evidence_ref = _write_degrade_record(
            prefix or f"evidence/{session_id}/{run_id}", str(exc), evidence_store
        )
        stages.extend(("COMPILE", "FAILED_STAGE"))
        sandbox = SandboxResult(
            passed=False, cases=(), log_ref="", snapshot_ref=None,
            compiled_hash="", reason_code=reason, evidence_ref=evidence_ref,
            evidence_refs=EvidenceRefs("", "", None, (), "", ""),
            stages=tuple(stages[2:]),
        )

    return _finalize(
        key, passed, guard, contract, sandbox, reason, evidence_ref,
        tuple(stages), repair_count, started,
    )


def on_sandbox_unavailable(
    spec: Mapping[str, JsonValue],
    source_ref: str,
    manifest_draft: Mapping[str, JsonValue],
    repair_count: int,
    *,
    session_id: str,
    run_id: str,
    evidence_store: EvidenceStorePort,
    source_store: SourceReaderPort | None = None,
    reason: str = "sandbox runtime unavailable",
) -> ValidateResult:
    """M11 触发的降级入口：A/B 全量静态校验 + 只读诊断，结果不判通过。"""
    guard = check_output_guard(spec, source_ref, manifest_draft, source_store=source_store)
    source_store = source_store or _default_source_store()
    try:
        code_ast = parse_tsx(source_store.get(source_ref).decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError):
        code_ast = object()
    contract = check_contract(spec, manifest_draft, code_ast)
    evidence_ref = _write_degrade_record(
        f"evidence/{session_id}/{run_id}", reason, evidence_store
    )
    empty = SandboxResult(
        passed=False, cases=(), log_ref="", snapshot_ref=None,
        compiled_hash="", reason_code=ReasonCode.SANDBOX_UNAVAILABLE,
        evidence_ref=evidence_ref,
        evidence_refs=EvidenceRefs("", "", None, (), "", ""),
        stages=("FAILED_STAGE",),
    )
    return ValidateResult(
        passed=False,
        output_guard=guard,
        contract_check=contract,
        sandbox_test=empty,
        reason_code=ReasonCode.SANDBOX_UNAVAILABLE,
        evidence_ref=evidence_ref,
        stages=("OUTPUT_GUARD", "CONTRACT_CHECK", "FAILED_STAGE"),
        repair_count=repair_count,
        handoff=repair_count >= MAX_REPAIR,
    )


def _finalize(
    key: tuple[str, ...],
    passed: bool,
    guard: OutputGuardReport,
    contract: ContractCheckReport | None,
    sandbox: SandboxResult | None,
    reason: ReasonCode | None,
    evidence_ref: str,
    stages: tuple[str, ...],
    repair_count: int,
    started: float,
) -> ValidateResult:
    if contract is None:
        contract = ContractCheckReport(
            passed=guard.passed, violations=(), spec_hash=sha256_hex(b""),
            evidence_ref=guard.evidence_ref, reason_code=None,
        )
    result = ValidateResult(
        passed=passed,
        output_guard=guard,
        contract_check=contract,
        sandbox_test=sandbox,
        reason_code=reason,
        evidence_ref=evidence_ref,
        stages=stages,
        repair_count=repair_count,
        handoff=repair_count >= MAX_REPAIR,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    with _CACHE_LOCK:
        _RESULT_CACHE[key] = result
    return result


def _default_source_store() -> SourceReaderPort:
    from services.sandbox_runner.ports import S3EvidenceStore

    return S3EvidenceStore()


def _write_degrade_record(prefix: str, detail: str, store: EvidenceStorePort) -> str:
    """写 SANDBOX_UNAVAILABLE 降级记录；写失败也不返回通过。"""
    try:
        ref = store.put(
            f"{prefix}/check_report.json",
            canonical_json({"code": "SANDBOX_UNAVAILABLE", "detail": detail}).encode("utf-8"),
        )
        return ref.key + "#sha256:" + ref.hash
    except (OSError, ValueError):
        return f"sandbox-unavailable:{sha256_hex(detail.encode('utf-8'))}"


def _hard_timeout_s(limits: Mapping[str, JsonValue]) -> int:
    value = _as_int(limits.get("sandbox_hard_timeout_ms", _DEFAULT_LIMITS["sandbox_hard_timeout_ms"]))
    return max(1, min(SANDBOX_HARD_TIMEOUT_S, value // 1000))


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _as_object(value: object) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return cast("dict[str, JsonValue]", value)
    return {}


