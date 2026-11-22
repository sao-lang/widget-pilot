"""M08 契约校验（R5/R6）：spec ↔ manifest ↔ 代码三方比对 + hash 绑定。

不一致一律 passed=False / CONTRACT_MISMATCH，且**不创建沙箱**（阶段 B 短路）；
校验只比对字段、枚举与 hash，不读取源码明文到报告（ADR-005）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

from packages.contracts.enums import IMPORT_ALLOWLIST, ReasonCode
from packages.contracts.types import JsonValue
from services.guardrails.capability import declared_capabilities, invalid_capabilities
from services.guardrails.output_ports import (
    ContractCheckReport,
    canonical_json,
    sha256_hex,
)
from services.guardrails.static_scan import (
    TsxAst,
    collect_capability_calls,
    collect_imports,
)

__all__ = ["check_contract", "compute_spec_hash"]

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "packages/contracts/widget_bundle.schema.json"
MANIFEST_SCHEMA = Draft202012Validator(
    json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["manifest"]
)


def compute_spec_hash(spec: Mapping[str, JsonValue]) -> str:
    """spec_hash = sha256(canonical_json(spec))，与字段顺序无关。"""
    return sha256_hex(canonical_json(spec).encode("utf-8"))


def check_contract(
    spec: Mapping[str, JsonValue],
    manifest_draft: Mapping[str, JsonValue],
    code_ast: object,
    *,
    evidence_ref: str = "",
) -> ContractCheckReport:
    """三方比对；任一不等即 CONTRACT_MISMATCH（不进沙箱）。"""
    spec_hash = compute_spec_hash(spec)
    violations: list[str] = []

    for error in MANIFEST_SCHEMA.iter_errors(cast("Mapping[str, JsonValue]", manifest_draft)):
        if error.validator == "required":
            required = [str(name) for name in error.validator_value]
            missing = sorted(set(required) - set(manifest_draft))
            violations.append(f"manifest.required: missing={missing}")
            continue
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        violations.append(f"manifest.{path}: {error.validator}")

    spec_caps = declared_capabilities(spec)
    manifest_caps = declared_capabilities(manifest_draft)
    spec_only = sorted(spec_caps - manifest_caps)
    manifest_only = sorted(manifest_caps - spec_caps)
    if spec_only or manifest_only:
        violations.append(f"capabilities: spec_only={spec_only} manifest_only={manifest_only}")
    invalid = invalid_capabilities(manifest_caps)
    if invalid:
        violations.append(f"capabilities: invalid={sorted(invalid)}")

    actual_imports = collect_imports(code_ast)
    # S7（review）：源码不可读 / AST 解析失败时 collect_imports 返回空集，
    # 会把"无法校验"误当成"import 全合法"。降级也必须 fail-closed：
    # 无有效 AST 即记一条 violation，绝不宣称 A/B 全量静态校验通过。
    if not isinstance(code_ast, TsxAst):
        violations.append("code_ast: unavailable (source unreadable or parse failed)")
    disallowed = sorted(actual_imports - frozenset(IMPORT_ALLOWLIST))
    if disallowed:
        violations.append(f"imports: disallowed={disallowed}")

    actual_calls = collect_capability_calls(code_ast) if isinstance(code_ast, TsxAst) else frozenset()
    undeclared = sorted(actual_calls - manifest_caps)
    if undeclared:
        violations.append(f"capability_calls: undeclared={undeclared}")

    if spec.get("grid") != manifest_draft.get("grid"):
        violations.append(f"grid: spec={spec.get('grid')} manifest={manifest_draft.get('grid')}")

    if manifest_draft.get("spec_hash") != spec_hash:
        violations.append("spec_hash: manifest value != sha256(canonical_json(spec))")

    passed = not violations
    return ContractCheckReport(
        passed=passed,
        violations=tuple(violations),
        spec_hash=spec_hash,
        evidence_ref=evidence_ref or f"contract:{spec_hash}",
        reason_code=None if passed else ReasonCode.CONTRACT_MISMATCH,
    )
