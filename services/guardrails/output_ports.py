"""M08 出护栏 / 契约校验 / 沙箱共享端口与结论对象（新端口协议，不改 M06 ports.py）。

所有结论对象不可变、可逐字段比较（I13 重复测试一致）；证据引用统一为
``{key}#sha256:{hex}``，读取方必须校验 hash（A17）。端口用 Protocol，方法体为省略号，
生产实现由 sandbox_runner 注入；测试替身只允许出现在 tests/support/。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from packages.contracts.enums import ReasonCode
from packages.contracts.types import JsonValue

__all__ = [
    "ContractCheckReport",
    "EvidenceRefs",
    "GuardJudge",
    "OutputGuardHit",
    "OutputGuardReport",
    "RepairCounterPort",
    "SandboxCase",
    "SandboxResult",
    "SourceReaderPort",
    "ValidateResult",
    "Violation",
    "canonical_json",
    "parse_object_ref",
    "sha256_hex",
]

_REF_SEPARATOR = "#sha256:"


class GuardJudge(StrEnum):
    """出 LLM 护栏六类判据（§3.4 A）。"""

    SCHEMA = "schema"
    CAPABILITY_ALLOWLIST = "capability_allowlist"
    FORBIDDEN_CONTENT = "forbidden_content"
    PII = "pii"
    INTENT_MATCH = "intent_match"
    SPEC_CONSISTENCY = "spec_consistency"


@dataclass(slots=True, frozen=True)
class Violation:
    """静态命中：只留节点类别、位置、片段 hash 与规则名，不存原文（ADR-005）。"""

    node_kind: str
    position: str
    snippet_hash: str
    rule: str


@dataclass(slots=True, frozen=True)
class OutputGuardHit:
    """护栏命中留证（§6.1 guard_hit 行，原文引用为 key+hash 而非明文）。"""

    judge: GuardJudge
    rule: str
    reason_code: ReasonCode
    source_ref: str
    position: str = ""
    snippet_hash: str = ""


@dataclass(slots=True, frozen=True)
class OutputGuardReport:
    """出 LLM 护栏结论；hit=False 才允许进入契约校验。"""

    passed: bool
    hit: bool
    judge: GuardJudge | None
    violations: tuple[Violation, ...]
    reason_code: ReasonCode | None
    evidence_ref: str
    hits: tuple[OutputGuardHit, ...] = ()


@dataclass(slots=True, frozen=True)
class ContractCheckReport:
    """三方比对结论；passed=False 时 reason_code 固定 CONTRACT_MISMATCH。"""

    passed: bool
    violations: tuple[str, ...]
    spec_hash: str
    evidence_ref: str
    reason_code: ReasonCode | None = None
    report_ref: str = ""


@dataclass(slots=True, frozen=True)
class EvidenceRefs:
    """证据包引用（每一项都是 ``key#sha256:{hex}``）。

    snapshot_ref = DOM 序列化 html（结构证据）；snapshot_png_ref = headless
    Chromium 真像素截图（R8/R13）。后者末尾追加且默认 None，降级路径构造兼容。
    """

    log_ref: str
    report_ref: str
    snapshot_ref: str | None
    case_refs: tuple[str, ...]
    compiled_ref: str
    compiled_hash: str
    snapshot_png_ref: str | None = None


@dataclass(slots=True, frozen=True)
class SandboxCase:
    """SandboxBundle.sandbox_test.cases[] 条目。"""

    name: str
    passed: bool
    detail_ref: str


@dataclass(slots=True, frozen=True)
class SandboxResult:
    """一次性沙箱结论；证据未写齐不得 passed=True（I10）。"""

    passed: bool
    cases: tuple[SandboxCase, ...]
    log_ref: str
    snapshot_ref: str | None
    compiled_hash: str
    reason_code: ReasonCode | None
    evidence_ref: str
    evidence_refs: EvidenceRefs
    stages: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ValidateResult:
    """validate_and_test 汇总结论；调度（REPAIR/HANDOFF）归 M07/M03。"""

    passed: bool
    output_guard: OutputGuardReport
    contract_check: ContractCheckReport
    sandbox_test: SandboxResult | None
    reason_code: ReasonCode | None
    evidence_ref: str
    stages: tuple[str, ...]
    repair_count: int
    handoff: bool = False
    elapsed_ms: int = 0


@runtime_checkable
class SourceReaderPort(Protocol):
    """按对象引用只读源码（M07 产出，M08 禁止改写）。"""

    def get(self, ref: str) -> bytes:
        """读取对象并按 ref 内 hash 校验；hash 不符抛错。"""
        ...


@runtime_checkable
class RepairCounterPort(Protocol):
    """回边计数只读端口；实现由 M03/M07 注入，M08 不自行累加。"""

    def count(self, run_id: str) -> int:
        """返回该 run 当前已用回边次数。"""
        ...


def sha256_hex(data: bytes) -> str:
    """字节摘要（sha256 hex），证据与 spec_hash 共用。"""
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: JsonValue | object) -> str:
    """稳定序列化：键排序、无多余空白、UTF-8 原样输出。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_object_ref(key: str, data: bytes) -> str:
    """生成带 hash 的对象引用。"""
    return f"{key}{_REF_SEPARATOR}{sha256_hex(data)}"


def parse_object_ref(ref: str) -> tuple[str, str]:
    """拆分引用为 (key, expected_hash)；格式非法 fail-closed。"""
    key, separator, digest = ref.partition(_REF_SEPARATOR)
    if not separator or not key or len(digest) != 64:
        raise ValueError(f"invalid object ref: {ref[:80]}")
    return key, digest
