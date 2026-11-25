"""P2 会话 widget 注册表 + 修改定位 RED 测试。

覆盖：
1. BundleStore/Service 的 list_by_session（租户隔离/state 过滤/limit/排序）
2. target 定位器五方式（last/ordinal/by_host/by_content/all）+ 定位不明反问精准信息
3. tool.session.* 能力注册 L0-L1-L2（渐进披露 + 用户身份不可覆盖）
4. 修改意图解析（内部 IntentCategory 扩展）

先写、先跑 RED（方法/模块不存在），再实现转 GREEN。
"""
from __future__ import annotations

from typing import cast

from packages.agent_core.types import JsonObject
from packages.contracts.enums import BundleState
from tests.support.bundles import (
    InMemoryBundleStore,
    RecordingArtifactAdoption,
    RecordingCorpusLink,
    RecordingEventSink,
    RecordingPreferenceRevocation,
)
from tests.support.control_plane import AuditLedger


def _bundle(bid: str, session: str, user: str = "u1", state: str = "GENERATED",
            intent: str = "桌面待办清单", grid: str = "4x2", created: str = "2026-11-25T10:00:00+00:00") -> JsonObject:
    return {
        "bundle_id": bid, "user_id": user, "session_id": session, "state": state,
        "spec": {"intent": intent, "grid": grid, "widget_type": "custom",
                 "layout_slots": [{"slot": "main", "content": "任务列表"}]},
        "created_at": created, "hash": "a" * 64, "versions": [],
    }


def _as_dict(value: object) -> dict[str, object]:
    """JsonObject 收窄：测试断言只接受 dict，非 dict 直接失败。"""
    assert isinstance(value, dict), value
    return cast(dict[str, object], value)


def _store_with(*bundles: JsonObject) -> InMemoryBundleStore:
    store = InMemoryBundleStore()
    for b in bundles:
        store.rows[str(b["bundle_id"])] = dict(b)
    return store


def _service(store):
    from services.control_plane.bundles.service import BundleService

    return BundleService(
        store=store, audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(), preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )


# ---------- 1. list_by_session ----------

def test_list_by_session_filters_tenant_and_state() -> None:
    """按会话列包：租户隔离 + session 过滤 + state 过滤。"""
    store = _store_with(
        _bundle("b1", "s1"), _bundle("b2", "s1", state="DELETED"),
        _bundle("b3", "s2"), _bundle("b4", "s1", user="u2"),
    )
    svc = _service(store)
    rows = svc.list_by_session("u1", "s1", None, 50)
    ids = {str(r["bundle_id"]) for r in rows}
    assert ids == {"b1", "b2"}, ids  # 不含 s2 的 b3、不含 u2 的 b4
    deleted = svc.list_by_session("u1", "s1", BundleState.DELETED.value, 50)
    assert {str(r["bundle_id"]) for r in deleted} == {"b2"}


def test_list_by_session_respects_limit_and_order() -> None:
    """limit 有界；按 created_at 排序（最新在前供 last 定位）。"""
    store = _store_with(
        _bundle("old", "s1", created="2026-11-24T10:00:00+00:00"),
        _bundle("new", "s1", created="2026-11-25T10:00:00+00:00"),
    )
    svc = _service(store)
    rows = svc.list_by_session("u1", "s1", None, 50)
    assert [r["bundle_id"] for r in rows] == ["new", "old"]
    assert [r["bundle_id"] for r in svc.list_by_session("u1", "s1", None, 1)] == ["new"]


# ---------- 2. target 定位器 ----------

def _registry_module():
    import importlib
    return importlib.import_module("services.control_plane.bundles.registry")


def test_resolve_target_last_and_all() -> None:
    """last 取最新；all 全返回。"""
    registry = _registry_module()
    bundles = [
        _bundle("old", "s1", created="2026-11-24T10:00:00+00:00"),
        _bundle("new", "s1", created="2026-11-25T10:00:00+00:00"),
    ]
    assert registry.resolve_target(bundles, "last")["bundle_id"] == "new"
    assert {b["bundle_id"] for b in registry.resolve_target(bundles, "all")} == {"old", "new"}


def test_resolve_target_ordinal() -> None:
    """ordinal 按创建时间正序定位（用户直觉），与 store 的降序输入无关。"""
    registry = _registry_module()
    bundles = [
        _bundle("b1", "s1", created="2026-11-25T10:00:00+00:00"),
        _bundle("b2", "s1", created="2026-11-25T10:01:00+00:00"),
        _bundle("b3", "s1", created="2026-11-25T10:02:00+00:00"),
    ]
    # 含逆序输入，确保按 created_at 升序而非列表顺序。
    assert registry.resolve_target(list(reversed(bundles)), "ordinal", ordinal=1)["bundle_id"] == "b1"
    assert registry.resolve_target(bundles, "ordinal", ordinal=2)["bundle_id"] == "b2"
    assert registry.resolve_target(bundles, "ordinal", ordinal=3)["bundle_id"] == "b3"


def test_resolve_target_by_host_and_content() -> None:
    """by_host/by_content 按 spec intent/layout 文本匹配。"""
    registry = _registry_module()
    bundles = [
        _bundle("desktop-task", "s1", intent="桌面待办清单"),
        _bundle("lock-weather", "s1", intent="锁屏天气卡片"),
    ]
    assert registry.resolve_target(bundles, "by_host", host="锁屏")["bundle_id"] == "lock-weather"
    assert registry.resolve_target(bundles, "by_content", content="待办")["bundle_id"] == "desktop-task"


def test_resolve_target_ambiguous_returns_precise_choices() -> None:
    """定位不明 → 返回每个 widget 精准信息供反问，不硬猜。"""
    registry = _registry_module()
    bundles = [
        _bundle("b1", "s1", intent="桌面待办清单", grid="4x2"),
        _bundle("b2", "s1", intent="桌面天气卡片", grid="2x2"),
    ]
    result = registry.resolve_target(bundles, "by_host", host="桌面")
    # 定位不明直接返回 choices（接口闭环，调用方无需另调 ambiguous_choices）
    assert isinstance(result, dict) and "choices" in result
    choices = result["choices"]
    assert isinstance(choices, list) and len(choices) == 2
    choices = [cast(dict, c) for c in choices]
    for c in choices:
        for key in ("bundle_id", "grid", "host", "data", "created_at"):
            assert key in c, f"反问缺精准信息 {key}"


# ---------- 3. 能力注册与披露 ----------

def test_session_capabilities_registered_and_progressive() -> None:
    """tool.session.* 注册进 REGISTRY；L0 摘要 / L1 schema / L2 执行。"""
    from packages.agent_core.tools import registry as reg

    # 测试环境可能未注册 → 用 unfreeze 显式注册（照既有测试范式）
    from services.orchestrator.capabilities import register_capabilities

    if reg.is_frozen():
        reg.unfreeze_registry()
    register_capabilities(session_query=_FakeSessionQuery())
    l0 = {str(c.get("id")) for c in reg.level0()}
    assert "tool.session.list_widgets" in l0
    detail = reg.capability_detail("tool.session.list_widgets")
    schema = _as_dict(detail.get("args_schema"))
    assert "session_id" in _as_dict(schema.get("properties"))


class _FakeSessionQuery:
    """会话查询端口：user_id 由调用方显式传入（handler 从 ContextVar 读）。"""

    def list_session_widgets(self, user_id: str, session_id: str, limit: int) -> list[JsonObject]:
        return [_bundle("b1", session_id, user=user_id)]

    def get_widget_detail(self, user_id: str, bundle_id: str) -> JsonObject | None:
        return None

    def list_session_attachments(self, user_id: str, session_id: str) -> list[JsonObject]:
        return []


def test_session_capability_user_id_not_overridable() -> None:
    """能力 args 不接受 user_id（防越权）：args_schema 无该字段。"""
    from packages.agent_core.tools import registry as reg
    from services.orchestrator.capabilities import register_capabilities

    if reg.is_frozen():
        reg.unfreeze_registry()
    register_capabilities(session_query=_FakeSessionQuery())
    schema = _as_dict(reg.capability_detail("tool.session.list_widgets").get("args_schema"))
    assert "user_id" not in _as_dict(schema.get("properties"))


# ---------- 4. 修改意图 ----------

def test_intent_category_extended_for_modify() -> None:
    """IntentCategory 扩 modify/continue/cancel（内部类型）。"""
    from services.guardrails.types import IntentCategory

    for name in ("WIDGET_MODIFY", "WIDGET_CONTINUE", "WIDGET_CANCEL"):
        assert hasattr(IntentCategory, name), f"缺意图枚举 {name}"


# ---------- 5. 独立 review S1/S2/S3 收敛 ----------

def test_L2_execution_ignores_model_supplied_user_id() -> None:
    """S2：invoke.load level=2 经端口执行；args 里的 user_id 不生效（防越权）。"""
    import json

    from packages.agent_core.tools import invoke
    from packages.agent_core.tools import registry as reg
    from packages.agent_core.types import Decision, DecisionKind
    from services.orchestrator.capabilities import register_capabilities
    from services.orchestrator.request_context import user_id_var

    if reg.is_frozen():
        reg.unfreeze_registry()
    register_capabilities(session_query=_FakeSessionQuery())
    token = user_id_var.set("u1")  # 生产由 dispatcher.process_one 设置
    try:
        result = invoke.load(Decision(
            kind=DecisionKind.LOAD_CAPABILITY,
            capability_id="tool.session.list_widgets",
            level=2,
            args={"session_id": "s1", "user_id": "u2", "limit": 5},
            surface="generation",
        ))
    finally:
        user_id_var.reset(token)
    assert result.ok is True
    payload = json.loads(result.output)
    assert len(payload["items"]) == 1
    assert payload["items"][0]["user_id"] == "u1"  # ContextVar u1 生效，args u2 无效


def test_modify_intent_maps_to_internal_category() -> None:
    """S3：描述含改/继续/取消 → 内部 IntentCategory 对应值（生产可达）。"""
    from services.guardrails.interpreter.rule import _intent_category

    assert _intent_category("把第二个日历改成 4x4") == "WIDGET_MODIFY"
    assert _intent_category("继续生成刚才那个清单") == "WIDGET_CONTINUE"
    assert _intent_category("取消生成那个天气卡片") == "WIDGET_CANCEL"
    assert _intent_category("桌面上加个 2x2 日历") == "WIDGET_GENERATION"


def test_zero_hit_returns_full_candidates_not_empty() -> None:
    """S1′：by_host 零命中也返回全部候选信息（反问不丢失精准信息）。"""
    from services.control_plane.bundles.registry import resolve_target

    bundles = [_bundle("b1", "s1", intent="桌面待办清单"), _bundle("b2", "s1", intent="锁屏天气")]
    result = resolve_target(bundles, "by_host", host="不存在的宿主")
    assert isinstance(result, dict) and "choices" in result
    choices = result["choices"]
    assert isinstance(choices, list) and len(choices) == 2


def test_modify_keyword_does_not_misclassify_improve() -> None:
    """N1′：「改善/改良」不误判为 WIDGET_MODIFY。"""
    from services.guardrails.interpreter.rule import _intent_category

    assert _intent_category("改善天气卡片的样式") == "WIDGET_GENERATION"


# ---------- P2 #36：user_id ContextVar 防越权 ----------

def test_L2_without_user_context_fails_closed() -> None:
    """无 per-request user 上下文时 L2 拒绝执行（不越权、不串号）。"""
    from packages.agent_core.tools import invoke
    from packages.agent_core.tools import registry as reg
    from packages.agent_core.types import Decision, DecisionKind
    from services.orchestrator.capabilities import register_capabilities
    from services.orchestrator.request_context import user_id_var

    if reg.is_frozen():
        reg.unfreeze_registry()
    register_capabilities(session_query=_FakeSessionQuery())
    import pytest

    from packages.agent_core.errors import CoreError

    token = user_id_var.set("")
    try:
        with pytest.raises(CoreError):
            invoke.load(Decision(
                kind=DecisionKind.LOAD_CAPABILITY,
                capability_id="tool.session.list_widgets",
                level=2, args={"session_id": "s1"}, surface="generation",
            ))
    finally:
        user_id_var.reset(token)


def test_context_var_reset_prevents_cross_task_leak() -> None:
    """user_id_var reset 后回到默认空串（run_forever 同线程复用不串号）。"""
    from services.orchestrator.request_context import user_id_var

    t1 = user_id_var.set("u1")
    assert user_id_var.get() == "u1"
    user_id_var.reset(t1)
    assert user_id_var.get() == ""


# ---------- P2 #36：修改意图写入 intent_id（生产 interpret 可达） ----------

def test_modify_description_produces_modify_intent_id() -> None:
    """「把第二个日历改成 4x4」走到 SlotFill 且 intent_id=WIDGET_MODIFY。"""
    from services.guardrails.interpreter.rule import RuleInterpreter

    result = RuleInterpreter().interpret("把第二个日历改成 4x4", (), {})
    assert result.kind == "compiled", (result.kind, result.missing_slots)
    assert result.instances[0].intent_id == "WIDGET_MODIFY"


def test_generation_description_keeps_generic_intent_id() -> None:
    """普通生成描述 intent_id 保持 WIDGET_GENERATION（不误标修改）。"""
    from services.guardrails.interpreter.rule import RuleInterpreter

    result = RuleInterpreter().interpret("桌面上加个 2x2 显示今天日期的卡片", (), {})
    assert result.kind == "compiled"
    assert result.instances[0].intent_id == "WIDGET_GENERATION"


def test_weather_query_still_rejected_after_intent_relax() -> None:
    """ADR-004 硬样本：纯天气疑问仍拒（放宽修改门不破坏护栏）。"""
    from services.guardrails.interpreter.rule import RuleInterpreter

    result = RuleInterpreter().interpret("今天天气怎么样", (), {})
    assert result.kind == "clarification"


# ---------- P2 复审 B3/S1 收敛 ----------

def test_real_interpreter_modify_description_has_data_slot() -> None:
    """B3：真实解释器下「把第二个日历改成 4x4」data 槽非空（日历类修改可达）。"""
    from services.guardrails.interpreter.rule import RuleInterpreter

    result = RuleInterpreter().interpret("把第二个日历改成 4x4", (), {})
    assert result.kind == "compiled"
    assert result.instances[0].slots.get("data"), result.instances[0].slots


def test_non_widget_object_not_compiled_under_modify_gate() -> None:
    """S1：非控件请求（取消订单/调整会议）不因 grid+host 被误编译。"""
    from services.guardrails.interpreter.rule import RuleInterpreter

    for desc in ("取消 2x2 桌面的订单", "调整 2x2 桌面的会议安排"):
        result = RuleInterpreter().interpret(desc, (), {})
        assert result.kind != "compiled", (desc, result.kind)


# ---------- P2 复审 Round2 NB1/N1 收敛 ----------

def test_clarification_answer_overrides_original_ordinal() -> None:
    """NB1：回答「第一个」覆盖原「第二个」定位，不再反问死循环。"""
    from services.control_plane.bundles.target_hint import parse_target_hint

    assert parse_target_hint("把第二个日历改成4x4 第一个")["ordinal"] == 1
    assert parse_target_hint("把第二个日历改成4x4 1")["ordinal"] == 1
    assert parse_target_hint("把第二个日历改成4x4 b1") == {
        "method": "by_content", "content": "b1"
    }


def test_compound_chinese_ordinal_twelve() -> None:
    """N1（Round2）：第十二个解析为 12，不是 10。"""
    from services.control_plane.bundles.target_hint import parse_target_hint

    assert parse_target_hint("把第十二个日历改成4x4")["ordinal"] == 12
