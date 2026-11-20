"""M06 输入快照与证据引用（§2.4 / §6.1 ValidationSnapshot / §6.5）。

- 快照只含「描述 / 附件引用 / grid」，**不含附件原文二进制或 extracted 全文**
  （§6.5 红线：扫描的是 extracted 文本，但快照不落原文）。
- 快照带 sha256，回放时校验；引用格式 `evidence/{session}/{run}/input_snapshot.json#sha256:<hex>`。

内存审计存储替身位于 tests/support/guardrails.py；生产由 validate_chain 注入。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import quote

from packages.contracts.enums import GridSpec
from services.guardrails.types import AttachmentView


@dataclass(slots=True, frozen=True)
class InputSnapshot:
    """输入快照（§6.1 ValidationSnapshot）：可序列化的最小证据集。"""

    description: str
    grid: str | None
    attachment_refs: tuple[str, ...]  # 仅 attachment_id 引用，不含原文/extracted 全文


@dataclass(slots=True, frozen=True)
class AuditEvent:
    """一条只追加审计事件（§2.4）。"""

    session_id: str
    run_id: str
    trace_id: str
    kind: str  # "VALIDATE" / "SNAPSHOT" / "DECISION"
    evidence_ref: str
    payload_hash: str  # 载荷 sha256，防篡改


@runtime_checkable
class AuditStorePort(Protocol):
    """只追加审计写入口：生产 PG 适配器与测试替身共用。

    生产没有读取全量事件的入口（M11 从 M04 AuditEvent 表读取），
    因此协议只要求 append；测试替身可额外提供 all_events 供断言。
    """

    def append(self, event: AuditEvent) -> None:
        """追加一条审计事件（只追加，无更新/删除 API）。"""
        ...


def build_snapshot(
    description: str,
    grid: GridSpec | str | None,
    attachments: list[AttachmentView],
) -> InputSnapshot:
    """构造输入快照（§6.5：只含描述/附件引用/grid，不含原文）。"""
    return InputSnapshot(
        description=description,
        grid=grid.value if isinstance(grid, GridSpec) else grid,
        attachment_refs=tuple(a.attachment_id for a in attachments),
    )


def snapshot_hash(snapshot: InputSnapshot) -> str:
    """计算快照 sha256（稳定序列化：sort_keys，确定性）。"""
    raw = json.dumps(
        {
            "description": snapshot.description,
            "grid": snapshot.grid,
            "attachment_refs": list(snapshot.attachment_refs),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evidence_ref(session_id: str, run_id: str, digest: str) -> str:
    """生成 evidenceRef（§6.1 引用格式带 hash）。

    session_id / run_id 做 URL 编码，避免含 '/' 或 '#' 时破坏引用路径与 hash 分隔。
    """
    safe_session = quote(session_id, safe="")
    safe_run = quote(run_id, safe="")
    return f"evidence/{safe_session}/{safe_run}/input_snapshot.json#sha256:{digest}"


def verify_snapshot(ref: str, snapshot: InputSnapshot) -> bool:
    """回放校验：ref 中的 sha256 是否与快照重算一致。"""
    marker = "#sha256:"
    if marker not in ref:
        return False
    expected = ref.split(marker, 1)[1]
    return expected == snapshot_hash(snapshot)
