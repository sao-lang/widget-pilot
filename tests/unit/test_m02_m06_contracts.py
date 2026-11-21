"""M02 §9.0 契约固化接线测试（v1.8，2026-11-21）。

锁定 M06 payload 固化的三件事（此前未接）：
- 义务2：error payload 的 { code, evidenceRef } 缺 evidenceRef 时被 schema 拒；
- 义务3：notice payload 的 spec_missing/spec_conflict 结构合法形状可过、畸形被拒；
- 义务1：/api/guardrails/hits 已入 openapi.yaml（/api/skills 随技能预取删除已移除）。

用 jsonschema 校验 events.schema.json：既校验 $defs 结构层，也校验事件级
discriminator（M3：kind=error/notice 的 string payload 兜底已移除，必填约束生效）。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from packages.agent_core.types import JsonObject, JsonValue

_CONTRACTS = Path(__file__).resolve().parents[2] / "packages" / "contracts"
_SCHEMA = json.loads((_CONTRACTS / "events.schema.json").read_text(encoding="utf-8"))
_OPENAPI = (_CONTRACTS / "openapi.yaml").read_text(encoding="utf-8")


def _validate_payload(def_name: str, payload: JsonObject) -> None:
    """用 $defs 下的具名 payload 定义校验结构。

    Args:
        def_name: $defs 中的定义名（errorPayload / noticePayload 等）。
        payload: 待校验的 payload 字典。
    """
    # 用完整 schema + $ref 定位具名定义（$defs 内的 $ref 需根 schema 解析）。
    jsonschema.validate(
        payload,
        {"$schema": _SCHEMA["$schema"], "allOf": [{"$ref": f"#/$defs/{def_name}"}], "$defs": _SCHEMA["$defs"]},
    )


class TestErrorPayload:
    """义务2：error payload 必带 evidenceRef。"""

    def test_error_with_evidence_ref_passes(self) -> None:
        """完整 { code, evidenceRef } 通过结构校验。"""
        _validate_payload(
            "errorPayload",
            {"code": "INTENT_OUT_OF_SCOPE", "evidenceRef": "evidence/x#sha256:abc"},
        )

    def test_error_without_evidence_ref_fails(self) -> None:
        """缺 evidenceRef → schema 拒（固化生效）。"""
        import pytest

        with pytest.raises(jsonschema.ValidationError):
            _validate_payload("errorPayload", {"code": "INTENT_OUT_OF_SCOPE"})


class TestNoticePayload:
    """义务3：notice payload 含 spec_missing/spec_conflict 结构。"""

    def test_spec_missing_structure_passes(self) -> None:
        """spec_missing.suggest_grid 为 GridSpec 数组时通过。"""
        _validate_payload(
            "noticePayload",
            {
                "code": "SPEC_MISSING",
                "message": "请选择规格",
                "spec_missing": {"suggest_grid": ["4x4", "2x2"]},
            },
        )

    def test_spec_missing_bad_grid_fails(self) -> None:
        """suggest_grid 含非法 grid → schema 拒。"""
        import pytest

        with pytest.raises(jsonschema.ValidationError):
            _validate_payload(
                "noticePayload",
                {
                    "code": "SPEC_MISSING",
                    "message": "请选择规格",
                    "spec_missing": {"suggest_grid": ["9x9"]},
                },
            )


def _validate_event(kind: str, payload: JsonValue) -> None:
    """用完整事件 schema 校验（含 discriminator allOf/if/then）。

    Args:
        kind: AgentEventKind 值。
        payload: 事件 payload（对象或字符串）。
    """
    jsonschema.validate(
        {
            "seq": 1,
            "sessionId": "s1",
            "runId": "r1",
            "ts": "2026-11-21T00:00:00Z",
            "kind": kind,
            "phase": "RUNNING",
            "payload": payload,
            "traceId": "t1",
        },
        _SCHEMA,
    )


class TestEventLevelDiscriminator:
    """M3：事件级 discriminator 让 error/notice 必填约束真正生效。"""

    def test_error_string_payload_rejected(self) -> None:
        """kind=error + string payload → 拒（string 兜底已移除）。"""
        import pytest

        with pytest.raises(jsonschema.ValidationError):
            _validate_event("error", "随便一个字符串")

    def test_error_missing_evidence_ref_rejected(self) -> None:
        """kind=error 缺 evidenceRef → 拒。"""
        import pytest

        with pytest.raises(jsonschema.ValidationError):
            _validate_event("error", {"code": "MODEL_ERROR"})

    def test_error_complete_payload_passes(self) -> None:
        """kind=error 完整 payload → 通过。"""
        _validate_event(
            "error", {"code": "MODEL_ERROR", "evidenceRef": "evidence/x#sha256:abc"}
        )

    def test_notice_string_payload_rejected(self) -> None:
        """kind=notice + string payload → 拒。"""
        import pytest

        with pytest.raises(jsonschema.ValidationError):
            _validate_event("notice", "not an object")


class TestOpenapiEndpoints:
    """义务1：M06 只读端点入 openapi 定稿。"""

    def test_guardrails_hits_in_openapi(self) -> None:
        """/api/guardrails/hits 已在 openapi.yaml。"""
        assert "/api/guardrails/hits:" in _OPENAPI

    def test_skills_removed_from_openapi(self) -> None:
        """ADR-003：技能预取已删除，/api/skills 不再入 openapi 契约。"""
        assert "/api/skills:" not in _OPENAPI
        assert "SkillManifest:" not in _OPENAPI
