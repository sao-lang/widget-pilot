"""M03 结构化输出严格校验（§11.5 / §4.1 L5）。

prompt-first + partial + final 严格校验：仅 final 用 JSON Schema（jsonschema）
严格校验，流中不触发副作用。失败产出结构化 RepairError{field,expected,got}。
"""
from __future__ import annotations

import json
from typing import cast

import jsonschema
from jsonschema import validators

from packages.agent_core.types import JsonObject, RepairError, ValidationResult
from packages.contracts.enums import ReasonCode


def _errors_from(exc: jsonschema.ValidationError) -> list[RepairError]:
    """把一条校验错误（连同其 context 子错误）摊平为 RepairError 列表。"""
    errors: list[RepairError] = []
    for err in exc.context or [exc]:
        # 有子错误时展开全部，否则退化为单条当前错误
        field = ".".join(str(p) for p in err.absolute_path) or "<root>"
        expected = str(err.validator_value)
        got = repr(err.instance)
        # got 截断，避免把整个对象塞进错误
        if len(got) > 120:
            got = got[:120] + "..."
        errors.append(RepairError(field=field, expected=expected, got=got))
    return errors


def validate(payload: str, schema: JsonObject) -> ValidationResult:
    """final 严格校验：payload 是 JSON 字符串，schema 是 JSON Schema 对象。"""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as e:
        return ValidationResult(
            ok=False,
            errors=[RepairError(field="<json>", expected="valid JSON", got=str(e))],
            code=ReasonCode.CONTRACT_MISMATCH,
        )
    validator_cls = validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        flat: list[RepairError] = []
        for e in errors:
            # 每条 jsonschema 错误可能携带 context 子错误，逐条摊平汇总
            flat.extend(_errors_from(e))
        return ValidationResult(ok=False, errors=flat, code=ReasonCode.CONTRACT_MISMATCH)
    # WidgetSpec schema 的顶层为 object；校验通过后收口为 JsonObject
    return ValidationResult(ok=True, data=cast(JsonObject, data))


def validate_manifest(spec: JsonObject, manifest: JsonObject) -> ValidationResult:
    """T12：manifest.capabilities 必须与 spec.capabilities 一致；否则 CONTRACT_MISMATCH。"""
    errors: list[RepairError] = []
    # capabilities 在契约中是字符串数组；cast 收口 JsonValue 后取集合比较（规则 3）
    spec_caps = set(cast(list[str], spec.get("capabilities", [])))
    man_caps = set(cast(list[str], manifest.get("capabilities", [])))
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
        return ValidationResult(ok=False, errors=errors, code=ReasonCode.CONTRACT_MISMATCH)
    return ValidationResult(ok=True, data=manifest)
