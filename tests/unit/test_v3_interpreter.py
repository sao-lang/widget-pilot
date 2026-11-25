"""v3 三层解释器 TDD（ADR-016）：规则层 + 三层降级判据。

首版只验理解层本身（不接 validate_chain、不依赖真权重）：
- RuleInterpreter 三件套：共现消歧 / 分散槽位聚合 / 后置 grid 分配
- LocalModelInterpreter（mock 注入）+ 三层降级判据与按层可观测
- 模型存 podman 卷挂载：manifest 校验与缺权重 fail-fast
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class _FakeAttachment:
    id: str
    role: str = ""
    insight_ref: str = ""


def _import(name: str):
    import importlib
    return importlib.import_module(name)


# ---------- 结构 ----------

def test_interpreter_public_api() -> None:
    mod = _import("services.guardrails.interpreter")
    for symbol in ("Interpreter", "RuleInterpreter", "LocalModelInterpreter",
                   "InterpreterRouter", "SlotFill", "InterpretResult", "LayerUsed"):
        assert hasattr(mod, symbol), f"缺导出符号 {symbol}"


def test_slot_fill_frozen_and_evidence() -> None:
    SlotFill = _import("services.guardrails.interpreter.types").SlotFill
    fill = SlotFill(
        intent_id="widget.clock",
        instance_id="h:0",
        slots={"grid": "2x2", "data": "date", "host": "桌面"},
        missing_slots=(),
        evidence=("grid@12:2x2",),
    )
    assert fill.slots["grid"] == "2x2"
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        fill.instance_id = "h:1"  # frozen dataclass：字段不可重新赋值


# ---------- 规则层三件套（实测反例） ----------

def test_host_alias_not_counted_as_two_instances() -> None:
    """「桌面+卡片」是同一 widget 两种叫法 → 1 个实例（v2 实测假阳性）。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret("桌面上加个 2x2 显示今天日期的卡片", (), {})
    assert result.kind == "compiled"
    assert len(result.instances) == 1


def test_scattered_slots_aggregate_into_one_instance() -> None:
    """grid 在句尾「尺寸就 2x2 吧」也归入同一 widget。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret(
        "我在桌面上想要一个能看到倒计时的卡片，尺寸就 2x2 吧，倒计时到本周五晚上八点", (), {}
    )
    assert result.kind == "compiled"
    assert len(result.instances) == 1
    assert result.instances[0].slots.get("grid") == "2x2"


def test_postposed_grids_distributed_not_new_instances() -> None:
    """「规格分别 4x4 和 2x1」按实例顺序分配，不新增实例。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret(
        "我想要在桌面有个显示日期的大卡片，另外锁屏也放一个倒计时，规格分别 4x4 和 2x1", (), {}
    )
    assert result.kind == "compiled"
    assert len(result.instances) == 2
    assert [i.slots["grid"] for i in result.instances] == ["4x4", "2x1"]


def test_multi_widget_two_grids_two_instances() -> None:
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret(
        "主屏放个 2x2 的日期卡片和 4x4 的购物清单", (), {}
    )
    assert result.kind == "compiled"
    assert len(result.instances) == 2


def test_missing_grid_is_clarification() -> None:
    """缺 grid 走追问，不降级模型。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret("帮我在桌面做个显示日期的小组件", (), {})
    assert result.kind == "clarification"
    assert "grid" in result.missing_slots


def test_oos_not_touched_by_interpreter() -> None:
    """越界由 L1 在前短路；解释器本身不产拒绝类。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret("今天天气怎么样", (), {})
    assert result.kind != "compiled"


# ---------- 三层降级 ----------

def test_router_uses_rule_first_zero_model() -> None:
    types = _import("services.guardrails.interpreter.types")
    Router = _import("services.guardrails.interpreter.router").InterpreterRouter
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter

    class _NeverCalledLocal:
        def interpret(self, *a, **k):
            pytest.fail("规则层已命中，不应调本地模型")

    router = Router(rule=RuleInterpreter(), local_model=_NeverCalledLocal())
    result = router.interpret("桌面上加个 2x2 显示今天日期的卡片", (), {})
    assert result.layer_used == types.LayerUsed.RULE
    assert result.kind == "compiled"


def test_router_falls_to_local_when_rule_low_confidence() -> None:
    types = _import("services.guardrails.interpreter.types")
    Router = _import("services.guardrails.interpreter.router").InterpreterRouter
    LocalModelInterpreter = _import("services.guardrails.interpreter.local_model").LocalModelInterpreter

    class _FakeWeight:
        def tag(self, text: str) -> dict:
            # 值必须是描述中真实 token（review B2 取值域校验）
            return {"grid": "2x2", "data": "日期", "host": "桌面", "confidence": 0.8}

    model = LocalModelInterpreter(weights=_FakeWeight())
    router = Router(rule=None, local_model=model)
    result = router.interpret("给我整一个能显示日期的小玩意，规格 2x2，桌面", (), {})
    assert result.layer_used == types.LayerUsed.LOCAL
    assert result.kind == "compiled"


def test_router_cloud_fallback_when_all_above_fail() -> None:
    types = _import("services.guardrails.interpreter.types")
    Router = _import("services.guardrails.interpreter.router").InterpreterRouter

    class _AlwaysFail:
        def interpret(self, *a, **k):
            return types.InterpretResult.fallback("stub-fail")

    router = Router(rule=_AlwaysFail(), local_model=_AlwaysFail())
    result = router.interpret("帮我总结下这个文档然后放到一个 2x2 的卡片里", (), {})
    assert result.layer_used == types.LayerUsed.CLOUD


# ---------- 权重校验（podman 卷挂载路径） ----------

def test_weights_missing_fails_closed() -> None:
    """无权重 → Fallback(LAYER_UNAVAILABLE)，禁止 bigram 冒充。"""
    types = _import("services.guardrails.interpreter.types")
    LocalModelInterpreter = _import("services.guardrails.interpreter.local_model").LocalModelInterpreter
    model = LocalModelInterpreter(weights=None, path="/infra/models/slot-tagger/v1")
    result = model.interpret("桌面上加个 2x2 的日期卡片", (), {})
    assert result.kind == "fallback"
    assert result.reason == types.FallbackReason.LAYER_UNAVAILABLE


def test_manifest_sha256_mismatch_fails() -> None:
    """权重文件 hash 与 manifest 不符 → fail-closed，不加载。"""
    loader = _import("services.guardrails.interpreter.weights")
    with pytest.raises(ValueError):
        loader.load_weights("/infra/models/slot-tagger/v1", expect_sha256="0" * 64)


# ---------- 独立 review 回归（Round 1 Blocker/Should） ----------

def test_data_slot_is_real_token_not_regex_pattern() -> None:
    """review B1：data 槽位必须是描述中的真实命中 token，不是正则串。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    desc = "桌面上加个 2x2 显示今天日期的卡片"
    result = RuleInterpreter().interpret(desc, (), {})
    assert result.kind == "compiled"
    for inst in result.instances:
        data = inst.slots.get("data", "")
        assert data and "|" not in data
        assert data in desc, f"data 应是描述原文 token，实际={data!r}"


def test_local_model_rejects_illegal_grid_and_out_of_scope_tag() -> None:
    """review B2：非法 grid / 越界键 / 低置信 → fallback，不硬塞。"""
    types = _import("services.guardrails.interpreter.types")
    LocalModelInterpreter = _import("services.guardrails.interpreter.local_model").LocalModelInterpreter

    class _BadGrid:
        def tag(self, text): return {"grid": "999x999", "confidence": 0.9}

    class _OOSTag:
        def tag(self, text): return {"grid": "2x2", "intent": "OUT_OF_SCOPE", "confidence": 0.9}

    class _LowConf:
        def tag(self, text): return {"grid": "2x2", "confidence": 0.3}

    for weights in (_BadGrid(), _OOSTag(), _LowConf()):
        r = LocalModelInterpreter(weights=weights).interpret(
            "桌面上加个 2x2 的日期卡片，桌面", (), {})
        assert r.kind == "fallback"
        assert r.layer_used == types.LayerUsed.LOCAL


def test_alias_variants_single_instance() -> None:
    """review S2：负一屏卡片 / 锁屏的服务卡片 / 主屏上的万能卡片 = 1 实例。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    for desc in (
        "负一屏卡片放个 2x2 的日期",
        "锁屏的服务卡片加个 2x2 的倒计时",
        "主屏上的万能卡片放个 4x4 的清单",
    ):
        result = RuleInterpreter().interpret(desc, (), {})
        assert result.kind == "compiled", desc
        assert len(result.instances) == 1, (desc, len(result.instances))


def test_oos_with_host_and_grid_not_compiled() -> None:
    """review S1：越界动作+宿主+grid 不得被解释器编译放行。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    for desc in (
        "帮我总结下这个文档然后放到一个 2x2 的卡片里",
        "写个爬虫脚本放到桌面卡片 规格2x2",
    ):
        result = RuleInterpreter().interpret(desc, (), {})
        assert result.kind != "compiled", desc


# ---------- Round 2 独立 review 回归（Should S1/S3/S4/S5/S6） ----------

def test_positive_widget_with_query_tone_not_rejected() -> None:
    """review S1：含「怎么样/多少」但明确生成 widget 的请求不得误拒。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    for desc in (
        "桌面放个 2x2 的天气卡片，显示今天天气怎么样",
        "桌面上加个 2x2 的股票卡片，看看股票多少",
    ):
        result = RuleInterpreter().interpret(desc, (), {})
        assert result.kind == "compiled", desc


def test_longest_host_term_single_instance() -> None:
    """review S3：「主屏幕上的万能卡片」不得拆成两个宿主。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret("主屏幕上的万能卡片放个 4x4 的清单", (), {})
    assert result.kind == "compiled"
    assert len(result.instances) == 1
    assert result.instances[0].slots["host"] == "主屏幕"


def test_data_nearest_host_not_reused() -> None:
    """review S4：多实例 data 按最近宿主归属，不复用尾 token。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter
    result = RuleInterpreter().interpret(
        "桌面放个 2x2 的天气卡片，锁屏再放个 4x4 的日期卡片", (), {})
    assert result.kind == "compiled"
    assert [i.slots["data"] for i in result.instances] == ["天气", "日期"]


def test_confidence_nonfinite_is_fallback() -> None:
    """review S6：NaN / inf / bool 置信度 fail-closed，不绕过 U-4 阈值。"""
    LocalModelInterpreter = _import("services.guardrails.interpreter.local_model").LocalModelInterpreter
    for conf in (float("nan"), float("inf"), True):
        class _W:
            def __init__(self, value): self.value = value

            def tag(self, text): return {"grid": "2x2", "confidence": self.value}

        r = LocalModelInterpreter(weights=_W(conf)).interpret(
            "桌面上加个 2x2 的日期卡片", (), {})
        assert r.kind == "fallback", conf


def test_rule_interpreter_integrates_attachment_tools() -> None:
    """附件规划器必须接进解释器：CSV 附件产出 rows 槽位。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter

    class _Att:
        id = "a1"; kind = "text"; role = "data_source"; insight_ref = ""

    class _Tools:
        def process_data_source(self, att):
            return {"format": "csv", "rows": [["日期", "2026-11-25"]], "row_count": 1}

    r = RuleInterpreter(attachment_tools=_Tools())
    result = r.interpret("用我传的 CSV 当数据源，做个 2x2 的日期卡片", (_Att(),), {})
    assert result.kind == "compiled"
    assert any("rows" in i.slots for i in result.instances)


def test_rule_interpreter_skips_attachment_when_not_referenced() -> None:
    """描述未提附件 → 不调工具，产物不受附件影响。"""
    RuleInterpreter = _import("services.guardrails.interpreter.rule").RuleInterpreter

    class _Att:
        id = "a1"; kind = "image"; role = "visual_reference"; insight_ref = ""

    class _NeverCalled:
        def process_visual(self, att):
            pytest.fail("描述未提附件，不应调用视觉工具")

    r = RuleInterpreter(attachment_tools=_NeverCalled())
    result = r.interpret("桌面上加个 2x2 的日期卡片", (_Att(),), {})
    assert result.kind == "compiled"
