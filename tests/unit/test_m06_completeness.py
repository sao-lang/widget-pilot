"""M06 功能完整性补全测试：槽位检查 / error 事件 / 审计快照 / suggest_grid 全特征过滤。"""

from __future__ import annotations

from packages.contracts.enums import GridSpec, ReasonCode
from services.guardrails.audit import build_snapshot, verify_snapshot
from services.guardrails.grid_resolver import resolve_grid
from services.guardrails.semantic import validate_semantic
from services.guardrails.types import AttachmentView, EventPayload, ValidationResult
from services.orchestrator.validate_chain import validate_chain as _raw_validate_chain
from tests.support import (
    GuardrailStore,
    InMemoryAuditStore,
    InMemoryGuardHitStore,
    IntentRouteLogStore,
)


def validate_chain(
    session_id: str,
    run_id: str,
    description: str,
    grid: GridSpec | str | None,
    attachments: list[AttachmentView],
    intent: str,
    budget_left: float,
    **kwargs: object,
) -> ValidationResult:
    """测试入口：为每次调用注入全新内存留证存储（原生产内置桩已移除）。"""
    stores: dict[str, GuardrailStore] = {
        "hit_store": InMemoryGuardHitStore(),
        "route_log": IntentRouteLogStore(),
        "audit_store": InMemoryAuditStore(),
    }
    return _raw_validate_chain(
        session_id,
        run_id,
        description,
        grid,
        attachments,
        intent,
        budget_left,
        **{**stores, **kwargs},
    )


# ── R1 描述充分性槽位检查 ───────────────────────────────────────────────────


def test_r1_slot_check_requires_widget_or_feature() -> None:
    """R1：既无控件类型又无关键特征 → INPUT_INVALID + 具体建议。"""
    result = validate_semantic("随便做一个东西出来吧", [], None)
    assert result.ok is False
    assert result.reason_code is ReasonCode.INPUT_INVALID
    assert result.suggestions


def test_r1_slot_check_accepts_widget_only() -> None:
    """R1：有控件类型（日历）+ 规格 → 放行。"""
    result = validate_semantic("帮我做一个日历控件出来", [], GridSpec._4X4)
    assert result.ok is True


def test_r1_slot_check_accepts_feature_only() -> None:
    """R1：长尾描述无显式控件词但有特征（显示日期）→ 放行，交级联裁定。"""
    result = validate_semantic("给我整一个能显示日期的小玩意", [], GridSpec._4X4)
    assert result.ok is True


# ── §4.2 error 通道 ────────────────────────────────────────────────────────


def test_error_event_emitted_for_out_of_scope() -> None:
    """§4.2：越界终态推 error { code, evidenceRef }，且 evidenceRef 非空。"""
    events: list[EventPayload] = []
    result = validate_chain(
        "s",
        "r",
        "今天天气怎么样",
        None,
        [],
        "WIDGET_GENERATION",
        100.0,
        event_sink=events.append,
    )
    assert result.reason_code is ReasonCode.INTENT_OUT_OF_SCOPE
    err = next(e for e in events if e.kind == "error")
    assert err.payload["code"] == "INTENT_OUT_OF_SCOPE"
    assert err.payload["evidenceRef"]


def test_error_event_emitted_for_budget_exhausted() -> None:
    """§4.2：预算触顶终态推 error。"""
    events: list[EventPayload] = []
    result = validate_chain(
        "s",
        "r",
        "做一个能翻月的日历",
        "4x4",
        [],
        "WIDGET_GENERATION",
        0.0,
        event_sink=events.append,
    )
    assert result.reason_code is ReasonCode.BUDGET_EXHAUSTED
    err = next(e for e in events if e.kind == "error")
    assert err.payload["code"] == "BUDGET_EXHAUSTED"


def test_guard_code_distinguishes_hit_type() -> None:
    """§4.2：guard.code 按命中类型细分（非恒 GUARD_INPUT_HIT）。"""
    events: list[EventPayload] = []
    validate_chain(
        "s",
        "r",
        "今天天气怎么样",
        None,
        [],
        "WIDGET_GENERATION",
        100.0,
        event_sink=events.append,
    )
    guard = next(e for e in events if e.kind == "guard")
    assert guard.payload["code"] == "INTENT_OUT_OF_SCOPE"


# ── §6.1 审计快照 + evidenceRef ─────────────────────────────────────────────


def test_audit_snapshot_append_and_verify() -> None:
    """§6.1/§6.5：输入快照只含描述/附件引用/grid，带 hash 可回放校验。"""
    store = InMemoryAuditStore()
    validate_chain(
        "s",
        "r",
        "做一个能翻月的日历",
        "4x4",
        [],
        "WIDGET_GENERATION",
        100.0,
        audit_store=store,
    )
    events = store.all_events()
    assert len(events) == 1
    assert events[0].kind == "SNAPSHOT"
    assert events[0].evidence_ref.startswith("evidence/s/r/")

    snap = build_snapshot("做一个能翻月的日历", GridSpec._4X4, [])
    assert verify_snapshot(events[0].evidence_ref, snap) is True


def test_snapshot_excludes_attachment_original() -> None:
    """§6.5 红线：快照只存 attachment_id 引用，不含原文/extracted 全文。"""
    att = AttachmentView("a1", "background", 0.9, "这是一段提取的敏感原文不应入快照")
    snap = build_snapshot("做一个能翻月的日历", GridSpec._4X4, [att])
    assert snap.attachment_refs == ("a1",)
    assert "敏感原文" not in snap.description


def test_validation_result_carries_evidence_ref() -> None:
    """§4.2：ValidationResult 带 evidenceRef，指向带 hash 的快照。"""
    result = validate_chain(
        "s", "r", "做一个能翻月的日历", "4x4", [], "WIDGET_GENERATION", 100.0
    )
    assert result.evidence_ref is not None
    assert "#sha256:" in result.evidence_ref


# ── §3.4 G suggest_grid 全特征冲突过滤 ──────────────────────────────────────


def test_suggest_grid_filters_multi_row_list_conflict() -> None:
    """§3.4 G：偏好含「多行列表」冲突组合（2x1）→ 被过滤，不进入建议首位。"""
    res = resolve_grid(
        None,
        "做一个多行列表任务清单",
        preferred_grids=["2x1", "4x4"],
    )
    assert GridSpec._2X1 not in res.suggest_grid
    assert res.suggest_grid  # 仍给出合法建议


def test_suggest_grid_filters_wide_table_conflict() -> None:
    """§3.4 G：宽表格 + 1x2 冲突 → 1x2 被过滤。"""
    res = resolve_grid(None, "做一个宽表格展示面板", preferred_grids=["1x2", "4x2"])
    assert GridSpec._1X2 not in res.suggest_grid


# ── review 修复回归 ─────────────────────────────────────────────────────────


def test_s2_pii_adjacent_to_chinese_detected() -> None:
    """S2：手机号/身份证紧邻中文时不被 \\b 漏检。"""
    from services.guardrails.input_guard import check_input
    from services.guardrails.types import BudgetView

    for text in (
        "我的手机号是13800138000帮我做控件",
        # 成熟库真值校验：身份证必须是校验位合法的号码（旧夹具校验位错误）
        "身份证11010519491231002X帮我做控件",
    ):
        result = check_input(text, [], "WIDGET_GENERATION", BudgetView(left=100.0))
        assert result.passed is False, text
        assert result.hits[0].rule_id.startswith("PII_")


def test_s3_appearance_only_description_rejected() -> None:
    """S3：纯外观词（样式/颜色）不构成关键特征，无控件信息应拒。"""
    result = validate_semantic("来个有样式的玩意", [], GridSpec._4X4)
    assert result.ok is False
    assert result.reason_code is ReasonCode.INPUT_INVALID
