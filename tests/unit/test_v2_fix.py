"""v2 复杂描述拷问出的 7 类缺陷 TDD（2026-11-26）。

每条都来自 v2 探针实测症状，先红后绿。不测实现细节，测用户可见行为。
"""
from __future__ import annotations

from packages.contracts.enums import GridSpec
from services.guardrails.interpreter.rule import (
    RuleInterpreter,
    _explicit_slots,
    _intent_category,
)

V01 = "对了上周三下午那个会开到很晚你记得吧，就是在会上我让你做的那个能被点一下就看到详情的桌面日程卡片，样式我后来想想还是别用彩色了改成那种很素的灰白配色比较好，尺寸其实一开始说的4x4后来我觉得太占地方了换成2x2吧，主要就是显示待办和会议别整那些花里胡哨的动效，哦对昨天同事问我这玩意能不能同步手机日历我说可以但其实还没做你先别管这个，交互上滑一下要能翻到下周，附件里那份课程表csv当数据源用，最后这个widget是放在主屏幕上的不是锁屏"
V19 = "把我那个锁屏卡片改成主屏上的4x3、样式换成科技蓝、结构上分成标题区数据区底部摘要三块"
V20 = "接着改上次那个东西——不是昨天那个是上上周那个我记得是个显示倒计时的widget，规格从2x2改成4x2吧颜色换成那种暗红的最好带点渐变但别太花，功能上除了倒计时我还想加上节假日提醒这个附件里那个txt里有具体规则你先校验一下合法性，交互上长按能重置点击能暂停这两个都要，如果上次的tsx还在生成中就用新的合并掉旧的把那个取消掉别留半成品，我描述得乱你别嫌烦实在分不清就用选择题让我选，对了空调温度帮我调一下这个文件不归你管我知道"
V04 = "之前给你传过一份股票数据的那个锁屏卡片——就是上周生成的第三个widget我记得是4x2的，现在行情天天跌我看着心烦，你把它从上周那个改成能显示涨跌超过5%就红色高亮的，样式也换成黑板风别用原来那个亮的，尺寸改成2x2省点地方，原来的数据源继续用别给我换了"


# ── 缺陷1：实例数失真 ──────────────────────────────────────────
def test_instance_count_single_widget_not_counted_by_anchor_words() -> None:
    """V01 只描述 1 个 widget，不应因宿主/指代词产 5 个。"""
    r = RuleInterpreter().interpret(V01)
    assert r.kind == "compiled"
    assert len(r.instances) == 1, f"单 widget 应产 1 个实例，实际 {len(r.instances)}"


def test_instance_count_explicit_three_stays_three() -> None:
    """显式说三个 widget 时仍产 3 个（不因过度合并退化）。"""
    desc = "一次做三个widget：2x2桌面时钟，4x2锁屏待办，2x1主屏电量"
    r = RuleInterpreter().interpret(desc)
    assert len(r.instances) == 3, f"显式三个应产 3，实际 {len(r.instances)}"


# ── 缺陷2：槽位贪婪误抽 ────────────────────────────────────────
def test_style_slot_not_capture_chatter() -> None:
    """V01 style 不应吞整段改写过程。"""
    slots = _explicit_slots(V01)
    style = slots.get("style", "")
    assert "你先别管" not in style and "想还是" not in style, f"style 误抽：{style!r}"


def test_function_slot_not_capture_chatter() -> None:
    """V01 function 不应抽到'你先别管这个'。"""
    slots = _explicit_slots(V01)
    assert "别管" not in slots.get("function", ""), f"function 误抽：{slots.get('function')!r}"


def test_interaction_slot_not_capture_demonstrative() -> None:
    """V20 interaction 不应抽到'这两个都要'。"""
    slots = _explicit_slots(V20)
    assert "这两个都要" not in slots.get("interaction", ""), (
        f"interaction 误抽：{slots.get('interaction')!r}"
    )


# ── 缺陷4：意图从句翻转 ────────────────────────────────────────
def test_cancel_clause_not_flip_main_intent() -> None:
    """V20 句尾'取消掉旧tsx合并新的'不应让整句变 CANCEL。"""
    r = RuleInterpreter().interpret(V20)
    assert all(i.intent_id != "WIDGET_CANCEL" for i in r.instances), (
        f"主句修改被从句翻成 CANCEL：{[i.intent_id for i in r.instances]}"
    )


def test_continue_word_in_data_source_not_flip_modify() -> None:
    """V04'数据源继续用'不应把修改意图翻成 CONTINUE。"""
    r = RuleInterpreter().interpret(V04)
    assert r.instances and r.instances[0].intent_id == "WIDGET_MODIFY", (
        f"应为 MODIFY，实际 {[i.intent_id for i in r.instances]}"
    )


def test_pure_cancel_not_compiled() -> None:
    """纯取消不应 compiled 成新 bundle。"""
    desc = "先停一下，取消掉刚才那个天气卡片的生成任务，别的先不做"
    r = RuleInterpreter().interpret(desc)
    assert r.kind != "compiled", "纯取消被编译，应走取消分支"


# ── 缺陷7：摘要裸词误伤 ────────────────────────────────────────
def test_summary_word_in_structure_not_rejected() -> None:
    """V19'底部摘要三块'是结构描述，不应触发创作类负向。"""
    r = RuleInterpreter().interpret(V19)
    assert not (r.kind == "clarification" and "intent" in r.missing_slots), (
        f"结构槽位'摘要'被误当越界：{r.missing_slots}"
    )


# ── 缺陷3：grid 规格扩容 ───────────────────────────────────────
def test_new_grid_specs_accepted() -> None:
    """2x3/3x2/4x3/5x3 应作为合法规格解析。"""
    for g in ("2x3", "3x2", "4x3", "5x3"):
        assert hasattr(GridSpec, "_" + g.replace("x", "X")), f"GridSpec 缺 {g}"
        r = RuleInterpreter().interpret(f"桌面放个{g}天气卡片")
        assert "grid" not in r.missing_slots, f"{g} 被判缺 grid"


# ── 缺陷5：style 进 spec ───────────────────────────────────────
def test_style_palette_reaches_spec() -> None:
    """颜色 token 应进 WidgetSpec.style.palette。"""
    from services.deterministic.compiler import compile_spec
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(
        intent_id="WIDGET_GENERATION", instance_id="h:0",
        slots={"grid": "2x2", "data": "天气", "host": "桌面", "style": "科技蓝"},
    )
    spec = compile_spec(fill)
    style = spec.get("style")
    assert isinstance(style, dict) and style.get("palette"), (
        f"style.palette 未填充：{style!r}"
    )


# ── 缺陷6：session_query 分支 ──────────────────────────────────
def test_session_query_branch() -> None:
    """'查一下本会话生成了哪些widget'应走查询分支，不编译新 widget。"""
    from services.deterministic.channel import run_deterministic

    result = run_deterministic(
        "查一下这个会话里生成了哪些widget",
        bundle_service=object(), evidence_store=object(), source_store=object(),
        docker_runner=object(),
    )
    assert isinstance(result, dict)
    assert result.get("branch") != "compiled", (
        f"查询请求不应编译成新 widget，实际 {result.get('branch')}"
    )


# ── Round1 复审补强 ────────────────────────────────────────────
def test_new_grid_specs_end_to_end_compile() -> None:
    """新规格须真过 assert_valid_spec，不能只停在解释器（B1 假绿）。"""
    from services.deterministic.compiler import assert_valid_spec, compile_spec
    for g in ("2x3", "3x2", "4x3", "5x3"):
        fill = __import__("services.guardrails.interpreter.types", fromlist=["SlotFill"]).SlotFill(
            intent_id="WIDGET_GENERATION", instance_id="h:0",
            slots={"grid": g, "data": "天气", "host": "桌面"},
        )
        assert assert_valid_spec(compile_spec(fill)) is None, f"{g} 未过真 schema 校验"


def test_count_ignores_demonstratives_and_structure() -> None:
    """count 不得从指代/结构量词抽（V19/V20/V08 残留）。"""
    for desc in ("这三个widget", "底部摘要三块", "第 三 个widget", "这两张图"):
        assert "count" not in _explicit_slots(desc), f"{desc} 误抽 count"


def test_summary_with_quantifier_rejected() -> None:
    """「摘要这份报告」是越界创作，须拒；「底部摘要三块」是结构，放行。"""
    r = RuleInterpreter().interpret("摘要这份报告，桌面放个2x2天气卡片")
    assert r.kind != "compiled"
    r2 = RuleInterpreter().interpret("桌面放个2x2天气卡片，结构分底部摘要三块")
    assert r2.kind == "compiled"


def test_cancel_clause_after_main_generation_not_flip() -> None:
    """「先取消旧的，再做个2x2天气卡片」主句是生成，不翻 CANCEL。"""
    assert _intent_category("先取消旧的，再做个2x2天气卡片") == "WIDGET_GENERATION"


# ── Round2 复审补强 ────────────────────────────────────────────
def test_new_grid_specs_pass_contract_gate() -> None:
    """新规格必须过 render_tsx + check_contract（B1 假绿根因）。"""
    from services.deterministic.compiler import (
        assert_tsx_contract_ok,
        compile_spec,
        render_tsx,
    )
    from services.guardrails.interpreter.types import SlotFill
    for g in ("2x3", "3x2", "4x3", "5x3"):
        spec = compile_spec(SlotFill(
            intent_id="WIDGET_GENERATION", instance_id="h:0",
            slots={"grid": g, "data": "天气", "host": "桌面"},
        ))
        assert_tsx_contract_ok(spec, render_tsx(spec))


def test_cancel_word_in_generation_sentence_not_flip() -> None:
    """同句 cancel 词不得翻转生成意图（Round2 B2/S3）。"""
    for desc in ("做个卡片之余把旧的取消掉", "把旧的取消掉再做个2x2天气卡片",
                 "先取消旧的再做个2x2天气卡片"):
        assert _intent_category(desc) == "WIDGET_GENERATION", desc


def test_count_ignores_newline_demonstratives() -> None:
    """count 指代排除须跨换行（Round2 S1）。"""
    for desc in ("第\n三个widget，桌面放个2x2天气卡片",
                 "那样三个widget，桌面放个2x2天气卡片"):
        assert "count" not in _explicit_slots(desc), desc


# ── Round3 复审补强 ────────────────────────────────────────────
def test_count_ignores_english_and_formal_demonstratives() -> None:
    """count 白名单化：英文/书面指代不得抽数量（Round3 B1）。"""
    for desc in ("aforementioned 三个widget，桌面放个2x2天气卡片",
                 "上述三个widget，桌面放个2x2天气卡片",
                 "those 三个widget，桌面放个2x2天气卡片",
                 "前面提过的三个widget，桌面放个2x2天气卡片"):
        assert "count" not in _explicit_slots(desc), desc


def test_session_query_with_grid_not_compiled() -> None:
    """带 grid 的纯查询不得编译成包（Round3 B2）。"""
    from services.deterministic.channel import _is_session_query

    for desc in ("查一下本会话生成了哪些widget，比如2x2的",
                 "看看本会话2x2的widget有哪些"):
        assert _is_session_query(desc), desc
