"""校验链装配入口（§4.3 validate_chain）：语义 → 护栏 → 级联 → 放行。

同步短事务纯判定：命中 / 挂起 / 拒绝即返回，不放行进模型（拒绝类）；
放行产 ValidationResult 交 M07，并产 guard/routing/plan 事件 payload。
能力加载（原 need_skill / 预取）已交给 loop 的 LOAD_CAPABILITY，不在本链。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from packages.contracts.enums import GridSpec, ModelTier, ReasonCode, WidgetType
from packages.contracts.types import JsonObject
from services.guardrails.audit import (
    AuditEvent,
    AuditStorePort,
    build_snapshot,
    evidence_ref,
    snapshot_hash,
)
from services.guardrails.guard_hit import GuardHitStore
from services.guardrails.input_guard import check_input
from services.guardrails.intent_cascade import classify_intent, is_out_of_scope
from services.guardrails.ports import (
    Embedder,
    IntentClassifier,
    IntentGateway,
    IntentRouteLogStorePort,
)
from services.guardrails.semantic import validate_semantic
from services.guardrails.semantic_router import INTENT_SAMPLES_VERSION
from services.guardrails.telemetry import IntentRouteLogRow
from services.guardrails.types import (
    AttachmentView,
    BudgetView,
    EventPayload,
    GridSource,
    GuardHitType,
    InputGuardHit,
    IntentCategory,
    RouteLayer,
    SpecConflict,
    SpecMissing,
    ValidationResult,
)

# ReasonCode 与命中类型的映射（§5.3）
_RC_BY_HIT = {
    "INJECTION": ReasonCode.GUARD_INPUT_HIT,
    "PII": ReasonCode.GUARD_INPUT_HIT,
    "INTENT": ReasonCode.INTENT_OUT_OF_SCOPE,
    "BUDGET": ReasonCode.BUDGET_EXHAUSTED,
}


def _emit(
    sink: Callable[[EventPayload], None] | None, kind: str, payload: JsonObject
) -> None:
    """产一条事件 payload；无 sink 时 no-op（纯判定仍可单测）。"""
    if sink is not None:
        sink(EventPayload(kind=kind, payload=payload))


# 需推 error 通道的终态原因码（§4.2：越界意图 / 预算触顶）
_ERROR_TERMINAL_CODES = frozenset(
    {ReasonCode.INTENT_OUT_OF_SCOPE, ReasonCode.BUDGET_EXHAUSTED}
)


def _terminal(
    reason_code: ReasonCode,
    trace_id: str,
    event_sink: Callable[[EventPayload], None] | None,
    ref: str | None = None,
) -> ValidationResult:
    """构造终态结论；越界/预算终态额外推 error 事件（§4.2 { code, evidenceRef }）。

    ref 为输入快照证据引用（带 hash）；M11/M15 据此回放。
    """
    if reason_code in _ERROR_TERMINAL_CODES:
        _emit(
            event_sink,
            "error",
            {"code": reason_code.value, "evidenceRef": ref},
        )
    return ValidationResult(
        ok=False, reason_code=reason_code, trace_id=trace_id, evidence_ref=ref
    )


def _notice_payload(
    reason_code: ReasonCode | None,
    suggestions: tuple[str, ...],
    spec_missing: SpecMissing | None,
    spec_conflict: SpecConflict | None,
) -> JsonObject:
    """构造完整 notice payload（§4.2）：message / clarify_hint / 建议项。"""
    payload: JsonObject = {
        "code": reason_code.value if reason_code else "",
        "message": suggestions[0] if suggestions else "",
    }
    if suggestions:
        payload["clarify_hint"] = suggestions[0]
    if spec_missing is not None:
        payload["spec_missing"] = {
            "suggest_grid": [g.value for g in spec_missing.suggest_grid]
        }
    if spec_conflict is not None:
        payload["spec_conflict"] = {
            "grid": spec_conflict.grid.value,
            "feature": spec_conflict.feature,
            "suggest_grid": spec_conflict.suggest_grid.value,
        }
    return payload


def validate_chain(
    session_id: str,
    run_id: str,
    description: str,
    grid: GridSpec | str | None,
    attachments: list[AttachmentView],
    intent: str,
    budget_left: float,
    hit_store: GuardHitStore,
    route_log: IntentRouteLogStorePort,
    audit_store: AuditStorePort,
    preferred_grids: list[str] | None = None,
    extracted_texts: list[str] | None = None,
    event_sink: Callable[[EventPayload], None] | None = None,
    embedder: Embedder | None = None,
    classifier: IntentClassifier | None = None,
    gateway: IntentGateway | None = None,  # L4 主 LLM 端口（M07 注入；默认结构化兜底）
    on_fallback: Callable[[str], None] | None = None,  # 降级告警回调（接 M11）
    clarified_grid: GridSpec | None = None,
    clarification_reply: bool = False,  # R2（v1.8 复审）：澄清重入标记，跳过描述充分性
) -> ValidationResult:
    """执行完整校验链，返回放行 / 挂起 / 拒绝结论。

    Args:
        session_id / run_id: 归属标识（留证用）。
        description: 用户描述。
        grid: 选择器规格（可选）。
        attachments: M05 附件视图。
        intent: L1 意图词表分类（INPUT_GUARD 前置）。
        budget_left: M11 预算余量。
        preferred_grids: 用户偏好（v1.4，只影响建议顺序）。
        extracted_texts: 附件/检索提取文本（不可信数据扫描）。
        event_sink: 事件 payload 接收回调。
        hit_store: 命中留证存储（生产为 PgGuardHitStore）。
        route_log: 意图路由日志存储（生产为 PG）。
        audit_store: 输入快照审计存储（生产为 PG 适配器，无内存默认）。
        embedder / classifier: L2/L3 可注入端口（测试计数 / 真实模型）。
        gateway: L4 主 LLM 端口（M07 注入；默认结构化兜底）。
        on_fallback: 降级告警回调（接 M11；默认仅日志）。
        clarified_grid: 用户经 /clarifications 回答后的规格（v1.3 D-6，溯源 clarified）。

    Returns:
        ValidationResult；ok=True 表示放行。
    """
    trace_id = f"{session_id}:{run_id}"
    store = hit_store
    rlog = route_log
    audit = audit_store

    # 输入快照 + 证据引用（§6.1 / §6.5）：只含描述/附件引用/grid，不含原文
    snapshot = build_snapshot(description, grid, attachments)
    snap_hash = snapshot_hash(snapshot)
    ref = evidence_ref(session_id, run_id, snap_hash)
    audit.append(AuditEvent(session_id, run_id, trace_id, "SNAPSHOT", ref, snap_hash))

    # 边界强制转换：REST 层传入的是字符串，内部一律用 GridSpec 枚举
    parsed_grid: GridSpec | None = None
    if grid is not None:
        try:
            parsed_grid = grid if isinstance(grid, GridSpec) else GridSpec(grid)
        except ValueError:
            # 非法枚举 → CONTRACT_MISMATCH（M1，v1.8）：这是入参契约错误，
            # 用户无法靠"补充描述"补救；用拒绝码让 dispatcher 转 FAILED，
            # 不进任何模型、也不误挂起成 AWAITING_INPUT 死等（原 INPUT_INVALID 会挂起）。
            return ValidationResult(
                ok=False,
                reason_code=ReasonCode.CONTRACT_MISMATCH,
                trace_id=trace_id,
                evidence_ref=ref,
            )

    # 【A3/I14 修正】step0：L1 越界判定先于语义层——拒绝类必须先短路，
    # 否则短描述或无 grid 的越界请求会先拿到 INPUT_INVALID/SPEC_MISSING，
    # A3「非控件请求被拒」永不兑现。此路径零模型调用（embedder/classifier 未构造）。
    if is_out_of_scope(description):
        # 与正常 guard 命中路径一致：构造 InputGuardHit + 写 guard_hit 留证，
        # 事件 hits 四字段齐全（§4.2 / I2 / I8），避免「guard 报命中但查不到留证」盲区。
        scope_hit = InputGuardHit(
            hit_type=GuardHitType.INTENT,
            rule_id="INTENT_GENERIC_CHAT",
            judgement="非控件生成意图，L1 越界短路",
            excerpt=description[:80],
        )
        store.log_hit(session_id, run_id, scope_hit, trace_id)
        _emit(
            event_sink,
            "guard",
            {
                "stage": "INPUT",
                "passed": False,
                "hits": [
                    {
                        "hit_type": scope_hit.hit_type.value,
                        "rule_id": scope_hit.rule_id,
                        "judgement": scope_hit.judgement,
                        "excerpt": scope_hit.excerpt,
                    }
                ],
                "code": ReasonCode.INTENT_OUT_OF_SCOPE.value,
            },
        )
        rlog.append(
            IntentRouteLogRow(
                session_id,
                run_id,
                RouteLayer.L1,
                IntentCategory.OUT_OF_SCOPE.value,
                None,
                0,
                INTENT_SAMPLES_VERSION,
                trace_id,
            )
        )
        return _terminal(ReasonCode.INTENT_OUT_OF_SCOPE, trace_id, event_sink, ref)

    # step7-10：进护栏四类（注入 / PII / 意图 / 预算），命中即短路
    # 【顺序修正】安全护栏先于语义层：注入/PII 描述多不含控件槽位，若先过语义层会被
    # 「描述不足」拦下、reason_code 失真（A1 期望 GUARD_INPUT_HIT）。安全红线优先，
    # 与 step0 越界前置短路同级，均为零模型调用。
    guard = check_input(
        description,
        extracted_texts or [a.extracted_text for a in attachments],
        intent,
        BudgetView(left=budget_left, user_id=""),
    )
    _emit(
        event_sink,
        "guard",
        {
            "stage": "INPUT",
            "passed": guard.passed,
            "hits": [
                {
                    "hit_type": h.hit_type.value,
                    "rule_id": h.rule_id,
                    "judgement": h.judgement,
                    "excerpt": h.excerpt,
                }
                for h in guard.hits
            ],
            "code": (
                "OK"
                if guard.passed
                else _RC_BY_HIT.get(
                    guard.hits[0].hit_type.value, ReasonCode.GUARD_INPUT_HIT
                ).value
            ),
        },
    )
    if not guard.passed and guard.hits:
        hit = guard.hits[0]
        store.log_hit(session_id, run_id, hit, trace_id)
        rc = _RC_BY_HIT.get(hit.hit_type.value, ReasonCode.GUARD_INPUT_HIT)
        return _terminal(rc, trace_id, event_sink, ref)

    # step2-6：语义层（描述充分性 / role / 规格获取与冲突）
    # 【A21 修正】用户已回答规格时溯源为 clarified，不重跑 resolve_grid。
    # 【B1/R2 修正】澄清重入（clarification_reply=True）跳过描述充分性复检——
    # 用户答 "4x4"/"四行四列" 本就是回答询问，不该再被描述长度/槽位判定拒绝。
    # R2-1：跳过的依据是该标记而非 grid 是否解析成功（role 澄清等纯文本回答也适用）；
    # R2-2：空 answer 由 API 层 min_length 拒绝，此处 description 非空且做注入扫描。
    if clarified_grid is not None:
        semantic = validate_semantic(
            description,
            attachments,
            clarified_grid,
            preferred_grids,
            grid_confirmed=clarification_reply,
        )
        if semantic.ok:
            semantic = replace(semantic, grid_source=GridSource.CLARIFIED)
    else:
        semantic = validate_semantic(
            description,
            attachments,
            parsed_grid,
            preferred_grids,
            grid_confirmed=clarification_reply,
        )
    if not semantic.ok:
        _emit(
            event_sink,
            "notice",
            _notice_payload(
                semantic.reason_code,
                semantic.suggestions,
                semantic.spec_missing,
                semantic.spec_conflict,
            ),
        )
        return ValidationResult(
            ok=False,
            reason_code=semantic.reason_code,
            trace_id=trace_id,
            evidence_ref=ref,
        )

    # step11：意图四级级联（L1 已由 intent 参数给出，此处再确认放行方向意图）
    decision = classify_intent(
        description,
        embedder=embedder,
        classifier=classifier,
        gateway=gateway,
        on_fallback=on_fallback,
    )
    rlog.append(
        IntentRouteLogRow(
            session_id,
            run_id,
            decision.route_layer,
            decision.intent.value,
            decision.similarity,
            decision.latency_ms,
            INTENT_SAMPLES_VERSION,
            trace_id,
            decision.fallback_reason,
        )
    )
    if decision.intent is IntentCategory.OUT_OF_SCOPE:
        return _terminal(ReasonCode.INTENT_OUT_OF_SCOPE, trace_id, event_sink, ref)

    # ADR-003：技能预取已删除（纯按需加载）。规程 id 由 registry.level0() 全量列给模型，
    # 经 LOAD_CAPABILITY 自主选择，不再随 ValidationResult 下发 recipe_ids。
    effective_grid = semantic.grid or parsed_grid
    assert effective_grid is not None  # 语义层通过时 grid 必已确定
    widget = _infer_widget(description, effective_grid)
    # ADR-003：need_skill 请求链已删除——模型经 LOAD_CAPABILITY 按需加载，
    # 五项闸门在 loop 内执行（capability_guard_adapter），审计写 capability_audit_log。
    effective_tier = decision.model_tier or ModelTier.STANDARD
    _emit(
        event_sink,
        "routing",
        {
            "widget_type": widget.value,
            "grid": effective_grid.value,
            "model_tier": effective_tier.value,
            "threshold_hit": decision.threshold_hit,
            "intent_normalized": decision.intent.value,
            "route_layer": decision.route_layer.value,
            "similarity": decision.similarity,
            "latency_ms": decision.latency_ms,
            "fallback_reason": decision.fallback_reason,
        },
    )
    _emit(event_sink, "plan", {"steps": ["语义校验", "进护栏", "意图级联（门禁）", "能力目录建议"]})

    return ValidationResult(
        ok=True,
        widget_type=widget,
        grid=effective_grid,
        grid_source=semantic.grid_source,
        model_tier=effective_tier,
        intent_normalized=decision.intent,
        attachment_refs=tuple(a.attachment_id for a in attachments),
        route_layer=decision.route_layer,
        similarity=decision.similarity,
        latency_ms=decision.latency_ms,
        trace_id=trace_id,
        evidence_ref=ref,
    )


def _infer_widget(description: str, grid: GridSpec | None) -> WidgetType:
    """从描述推断 widget_type（真实装配由意图归一化槽位覆盖）。"""
    hints = (
        ("日历", WidgetType.CALENDAR),
        ("时钟", WidgetType.CLOCK),
        ("闹钟", WidgetType.ALARM),
        ("任务", WidgetType.TASK),
    )
    for word, widget in hints:
        if word in description:
            return widget
    return WidgetType.CUSTOM
