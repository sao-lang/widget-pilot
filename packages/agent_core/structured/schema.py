"""M03 结构化输出严格校验（§11.5 / §4.1 L5）。

prompt-first + partial + final 严格校验：仅 final 用 JSON Schema（jsonschema）
严格校验，流中不触发副作用。失败产出结构化 RepairError{field,expected,got}。
"""
from __future__ import annotations

import json

import jsonschema

from packages.agent_core.types import RepairError, ValidationResult


def _errors_from(exc: jsonschema.ValidationError) -> list[RepairError]:
    errors: list[RepairError] = []
    for err in exc.context or [exc]:
        field = ".".join(str(p) for p in err.absolute_path) or "<root>"
        expected = str(err.validator_value)
        got = repr(err.instance)
        # got 截断，避免把整个对象塞进错误
        if len(got) > 120:
            got = got[:120] + "..."
        errors.append(RepairError(field=field, expected=expected, got=got))
    return errors


def validate(payload: str, schema: dict) -> ValidationResult:
    """final 严格校验：payload 是 JSON 字符串，schema 是 JSON Schema dict。"""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as e:
        return ValidationResult(
            ok=False,
            errors=[RepairError(field="<json>", expected="valid JSON", got=str(e))],
            code="CONTRACT_MISMATCH",
        )
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        flat: list[RepairError] = []
        for e in errors:
            flat.extend(_errors_from(e))
        return ValidationResult(ok=False, errors=flat, code="CONTRACT_MISMATCH")
    return ValidationResult(ok=True, data=data)


def validate_manifest(spec: dict, manifest: dict) -> ValidationResult:
    """T12：manifest.capabilities 必须与 spec.capabilities 一致；否则 CONTRACT_MISMATCH。"""
    errors: list[RepairError] = []
    spec_caps = set(spec.get("capabilities", []))
    man_caps = set(manifest.get("capabilities", []))
    if spec_caps != man_caps:
        errors.append(RepairError(
            field="capabilities",
            expected=str(sorted(spec_caps)),
            got=str(sorted(man_caps)),
        ))
    if spec.get("spec_version") != manifest.get("spec_version"):
        errors.append(RepairError(
            field="spec_version",
            expected=str(spec.get("spec_version")),
            got=str(manifest.get("spec_version")),
        ))
    if errors:
        return ValidationResult(ok=False, errors=errors, code="CONTRACT_MISMATCH")
    return ValidationResult(ok=True, data=manifest)
