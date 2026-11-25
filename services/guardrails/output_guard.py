"""M08 出 LLM 护栏六类判据（R1-R4）：schema / 能力白名单 / 禁止内容 / PII /
描述符合度 / 规格一致性。全部为确定性代码，零模型调用、零外部服务调用（除只读源码引用）。

ADR-004 去类型化后 widget_type 恒 custom，描述符合度基于开放特征槽位而非类型枚举。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

from packages.contracts.enums import GridSpec, ReasonCode, WidgetType
from packages.contracts.types import JsonValue
from services.guardrails.capability import declared_capabilities, invalid_capabilities
from services.guardrails.dangerous_call import detect_dangerous_calls
from services.guardrails.output_ports import (
    GuardJudge,
    OutputGuardHit,
    OutputGuardReport,
    SourceReaderPort,
    Violation,
    canonical_json,
    sha256_hex,
)
from services.guardrails.patterns.pii import RULES as PII_RULES
from services.guardrails.spec_conflicts import check_conflict
from services.guardrails.static_scan import (
    TsxAst,
    has_default_widget,
    parse_tsx,
    scan_forbidden,
)
from services.guardrails.validators import is_valid_pii

__all__ = ["check_output_guard"]

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "packages/contracts/widget_spec.schema.json"
SPEC_SCHEMA = Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))

# 开放特征槽位（ADR-004）：regex 命中意图 → 槽位文本至少出现一个同类词；
# 不消费 widget_type，不引入裁判模型。
_FEATURE_SLOT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"月视图|翻月|农历|日历|周视图|日视图", ("月", "日历", "日期", "周", "日")),
    (r"计时|倒计时|秒表|表盘|整点|时分秒|时钟", ("时间", "计时", "时钟", "秒")),
    (r"提醒|闹铃|重复|贪睡|闹钟", ("闹钟", "提醒", "闹铃")),
    (r"清单|勾选|优先级|截止|待办|任务", ("任务", "清单", "待办", "勾选")),
    (r"天气|气温|降雨|空气质量|预报|未来一周", ("天气", "气温", "预报")),
    (r"步数|心率|睡眠|运动|卡路里|体重", ("步数", "心率", "运动", "健康")),
    (r"股票|基金|金价|汇率|行情|涨跌", ("股票", "基金", "行情", "汇率")),
    (r"快递|物流|外卖|航班|路况|公交|地铁", ("快递", "物流", "航班", "路况")),
    (r"电量|电池|充电|流量|话费|网速", ("电量", "电池", "充电", "流量")),
)


def check_output_guard(
    spec: Mapping[str, JsonValue],
    source_ref: str,
    manifest_draft: Mapping[str, JsonValue],
    *,
    source_store: SourceReaderPort | None = None,
) -> OutputGuardReport:
    """六判据短路判定；hit=False 才允许进入契约校验。"""
    store = source_store or _default_source_store()
    try:
        source = store.get(source_ref).decode("utf-8")
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        violation = Violation("SourceRef", "0:0", sha256_hex(str(exc).encode()), "source_unreadable")
        return _fail(GuardJudge.SCHEMA, ReasonCode.GUARD_OUTPUT_HIT, source_ref, spec, [violation])

    ast = parse_tsx(source)

    schema_violations = _schema_violations(spec, ast)
    if schema_violations:
        return _fail(GuardJudge.SCHEMA, ReasonCode.GUARD_OUTPUT_HIT, source_ref, spec, schema_violations)

    capability_violations = _capability_violations(spec, manifest_draft)
    if capability_violations:
        return _fail(GuardJudge.CAPABILITY_ALLOWLIST, ReasonCode.CAPABILITY_DENIED, source_ref, spec, capability_violations)

    forbidden = scan_forbidden(ast)
    if forbidden:
        return _fail(GuardJudge.FORBIDDEN_CONTENT, ReasonCode.GUARD_OUTPUT_HIT, source_ref, spec, list(forbidden))

    # ADR-010 §3.2：危险接口检测在出护栏（模型输出后、沙箱执行前）再跑一遍。
    # 进护栏只扫用户描述，此处扫模型生成的源码（含动态构造 API 名 / 白名单外接口）。
    # 与进护栏同源词表 + device-apis.json 白名单，不做语义放行、不调模型。
    danger = detect_dangerous_calls(source, source_kind="code")
    if danger.hit:
        danger_violations = [
            Violation("DangerousCall", "0:0", sha256_hex(rule.encode()), rule)
            for rule in danger.violations
        ]
        return _fail(
            GuardJudge.FORBIDDEN_CONTENT, ReasonCode.GUARD_OUTPUT_HIT,
            source_ref, spec, danger_violations,
        )

    pii = _pii_violations(spec, source)
    if pii:
        return _fail(GuardJudge.PII, ReasonCode.GUARD_OUTPUT_HIT, source_ref, spec, pii)

    intent = _intent_violations(spec)
    if intent:
        return _fail(GuardJudge.INTENT_MATCH, ReasonCode.GUARD_OUTPUT_HIT, source_ref, spec, intent)

    consistency = _consistency_violations(spec)
    if consistency:
        return _fail(GuardJudge.SPEC_CONSISTENCY, ReasonCode.SPEC_CONFLICT, source_ref, spec, consistency)

    digest = sha256_hex((source_ref + canonical_json(spec)).encode("utf-8"))
    return OutputGuardReport(
        passed=True,
        hit=False,
        judge=None,
        violations=(),
        reason_code=None,
        evidence_ref=f"guard-pass:{digest}",
    )


def _default_source_store() -> SourceReaderPort:
    """生产默认：从环境读 S3 配置的只读实现；缺配置 fail-fast。"""
    from services.sandbox_runner.ports import S3EvidenceStore

    return S3EvidenceStore()


def _schema_violations(spec: Mapping[str, JsonValue], ast: TsxAst) -> list[Violation]:
    violations: list[Violation] = []
    for error in list(SPEC_SCHEMA.iter_errors(cast("Mapping[str, JsonValue]", spec)))[:20]:
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        violations.append(
            Violation("WidgetSpec", path, sha256_hex(f"{error.validator}:{path}".encode()), f"schema:{error.validator}")
        )
    if not has_default_widget(ast):
        violations.append(
            Violation("ExportDefaultDeclaration", "0:0", sha256_hex(b"default_export_not_widget"), "default_export_not_widget")
        )
    return violations


def _capability_violations(
    spec: Mapping[str, JsonValue], manifest_draft: Mapping[str, JsonValue]
) -> list[Violation]:
    violations: list[Violation] = []
    declared = declared_capabilities(manifest_draft)
    invalid = invalid_capabilities(declared)
    if invalid:
        violations.append(
            Violation("Capability", "manifest.capabilities", sha256_hex("|".join(sorted(invalid)).encode()), "capability_invalid")
        )
    if declared != declared_capabilities(spec):
        digest = sha256_hex("|".join(sorted(declared ^ declared_capabilities(spec))).encode())
        violations.append(
            Violation("Capability", "manifest.capabilities", digest, "capability_mismatch")
        )
    return violations


def _pii_violations(spec: Mapping[str, JsonValue], source: str) -> list[Violation]:
    candidates: list[str] = []
    intent = spec.get("intent")
    if isinstance(intent, str):
        candidates.append(intent)
    slots = spec.get("layout_slots")
    if isinstance(slots, Sequence):
        for slot in slots:
            if isinstance(slot, Mapping):
                for key in ("slot", "content"):
                    value = slot.get(key)
                    if isinstance(value, str):
                        candidates.append(value)
    candidates.append(source)

    violations: list[Violation] = []
    seen: set[tuple[str, str]] = set()
    for text in candidates:
        for rule in PII_RULES:
            for match in re.finditer(rule.regex, text):
                value = match.group()
                if not is_valid_pii(rule.rule_id, value):
                    continue
                digest = sha256_hex(value.encode())
                key = (rule.rule_id, digest)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    Violation("PII", f"text:{match.start()}", digest, f"pii:{rule.rule_id}")
                )
    return violations


def _intent_violations(spec: Mapping[str, JsonValue]) -> list[Violation]:
    violations: list[Violation] = []
    if spec.get("widget_type") != WidgetType.CUSTOM.value:
        violations.append(
            Violation("WidgetType", "spec.widget_type", sha256_hex(b"non_custom"), "widget_type_not_custom")
        )
    intent = spec.get("intent")
    if not isinstance(intent, str):
        return violations
    slot_text = " ".join(_slot_texts(spec))
    for index, (pattern, terms) in enumerate(_FEATURE_SLOT_TERMS):
        if re.search(pattern, intent) and not any(term in slot_text for term in terms):
            violations.append(
                Violation(
                    "IntentFeature",
                    "spec.layout_slots",
                    sha256_hex(f"{index}:{pattern}".encode()),
                    f"intent_feature_unreflected:{index}",
                )
            )
    return violations


def _slot_texts(spec: Mapping[str, JsonValue]) -> list[str]:
    slots = spec.get("layout_slots")
    if not isinstance(slots, Sequence):
        return []
    texts: list[str] = []
    for slot in slots:
        if isinstance(slot, Mapping):
            for key in ("slot", "content"):
                value = slot.get(key)
                if isinstance(value, str):
                    texts.append(value)
    return texts


def _consistency_violations(spec: Mapping[str, JsonValue]) -> list[Violation]:
    violations: list[Violation] = []
    grid_value = spec.get("grid")
    intent = spec.get("intent")
    if isinstance(grid_value, str) and isinstance(intent, str):
        try:
            grid = GridSpec(grid_value)
        except ValueError:
            grid = None
        if grid is not None and check_conflict(grid, intent):
            violations.append(
                Violation("SpecConflict", "spec.grid+intent", sha256_hex(f"{grid_value}:conflict".encode()), "spec_conflict")
            )
    if isinstance(grid_value, str) and "x" in grid_value:
        parts = grid_value.split("x")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            cells = int(parts[0]) * int(parts[1])
            slots = spec.get("layout_slots")
            count = len(slots) if isinstance(slots, Sequence) else 0
            if count > cells:
                violations.append(
                    Violation("SpecDensity", "spec.layout_slots", sha256_hex(f"{grid_value}:{count}".encode()), "slot_density_exceeded")
                )
    constraints = spec.get("constraints")
    slots = spec.get("layout_slots")
    if isinstance(constraints, Mapping) and isinstance(slots, Sequence):
        maximum = constraints.get("max_elements")
        if isinstance(maximum, int) and len(slots) > maximum:
            violations.append(
                Violation("SpecDensity", "spec.constraints.max_elements", sha256_hex(f"{maximum}:{len(slots)}".encode()), "max_elements_below_slots")
            )
    return violations


def _fail(
    judge: GuardJudge,
    reason: ReasonCode,
    source_ref: str,
    spec: Mapping[str, JsonValue],
    violations: list[Violation],
) -> OutputGuardReport:
    digest_input = {
        "source_ref": source_ref,
        "judge": judge.value,
        "spec": canonical_json(spec),
        "rules": [f"{item.rule}:{item.position}:{item.snippet_hash}" for item in violations],
    }
    evidence_ref = "guard:" + sha256_hex(canonical_json(digest_input).encode("utf-8"))
    return OutputGuardReport(
        passed=False,
        hit=True,
        judge=judge,
        violations=tuple(violations),
        reason_code=reason,
        evidence_ref=evidence_ref,
        hits=tuple(
            OutputGuardHit(judge, item.rule, reason, source_ref, item.position, item.snippet_hash)
            for item in violations
        ),
    )
