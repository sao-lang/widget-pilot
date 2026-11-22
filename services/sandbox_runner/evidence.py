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
    snapshot_png: bytes | None = None,
) -> EvidenceRefs:
    """写齐证据并返回带 hash 引用；case_refs 顺序与 cases 一致。

    snapshot_png 为 headless Chromium 真像素截图（R8/R13）；None 表示降级/
    失败路径无像素证据，不得伪造。追加在末尾以兼容既有 8 位位置调用。
    """
    report_ref = store.put(f"{prefix}/check_report.json", canonical_json(report).encode("utf-8"))
    log_ref = store.put(f"{prefix}/sandbox.log", bytes(log))
    snapshot_ref = (
        store.put(f"{prefix}/snapshot.html", bytes(snapshot)) if snapshot is not None else None
    )
    snapshot_png_ref = (
        store.put(f"{prefix}/snapshot.png", bytes(snapshot_png))
        if snapshot_png is not None and bytes(snapshot_png)[:8] == b"\x89PNG\r\n\x1a\n"
        else None
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
        snapshot_png_ref=(
            snapshot_png_ref.key + "#sha256:" + snapshot_png_ref.hash
            if snapshot_png_ref else None
        ),
    )


def verify_evidence_refs(store: EvidenceStorePort, refs: EvidenceRefs) -> bool:
    """逐项回读并校验 hash；任一缺失/篡改即 False。"""
    candidates = [
        refs.log_ref,
        refs.report_ref,
        refs.snapshot_ref,
        refs.snapshot_png_ref,
        *refs.case_refs,
        refs.compiled_ref,
    ]
    for ref in candidates:
        if ref is None:
            continue  # 可选证据（html/png）缺失不在此判失败，由 reason_code 决定
        if not ref:
            return False
        try:
            store.get(ref)
        except (ValueError, OSError, KeyError):
            return False
    return True
