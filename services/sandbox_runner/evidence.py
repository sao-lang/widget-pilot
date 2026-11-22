"""M08 证据包写入（R13/I10）：日志 / 快照 / 校验报告 / 逐例明细 → 对象存储。

所有引用带 sha256；读取时校验，缺证据或 hash 不符不得判 PASSED（A17）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from packages.contracts.types import JsonValue
from services.guardrails.output_ports import EvidenceRefs, canonical_json
from services.sandbox_runner.ports import EvidenceStorePort

__all__ = ["verify_evidence_refs", "write_evidence"]


def write_evidence(
    prefix: str,
    report: Mapping[str, JsonValue],
    log: bytes,
    snapshot: bytes | None,
    cases: Sequence[tuple[str, bool]],
    compiled_ref: str,
    compiled_hash: str,
    store: EvidenceStorePort,
) -> EvidenceRefs:
    """写齐证据并返回带 hash 引用；case_refs 顺序与 cases 一致。"""
    report_ref = store.put(f"{prefix}/check_report.json", canonical_json(report).encode("utf-8"))
    log_ref = store.put(f"{prefix}/sandbox.log", bytes(log))
    snapshot_ref = (
        store.put(f"{prefix}/snapshot.html", bytes(snapshot)) if snapshot is not None else None
    )

    case_refs: list[str] = []
    for name, passed in cases:
        detail = {"name": name, "passed": passed}
        stored = store.put(f"{prefix}/cases/{name}.json", canonical_json(detail).encode("utf-8"))
        case_refs.append(stored.key + "#sha256:" + stored.hash)

    return EvidenceRefs(
        log_ref=log_ref.key + "#sha256:" + log_ref.hash,
        report_ref=report_ref.key + "#sha256:" + report_ref.hash,
        snapshot_ref=snapshot_ref.key + "#sha256:" + snapshot_ref.hash if snapshot_ref else None,
        case_refs=tuple(case_refs),
        compiled_ref=compiled_ref,
        compiled_hash=compiled_hash,
    )


def verify_evidence_refs(store: EvidenceStorePort, refs: EvidenceRefs) -> bool:
    """逐项回读并校验 hash；任一缺失/篡改即 False。"""
    candidates = [
        refs.log_ref,
        refs.report_ref,
        refs.snapshot_ref,
        *refs.case_refs,
        refs.compiled_ref,
    ]
    for ref in candidates:
        if ref is None or not ref:
            return False
        try:
            store.get(ref)
        except (ValueError, OSError, KeyError):
            return False
    return True
