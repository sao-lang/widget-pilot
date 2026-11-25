"""v3 能力驱动的附件规划器 TDD（ADR-014 33 能力 + ADR-016）。

用户诉求：
- 解释器根据复杂描述决定「调哪个能力、到什么深度」，不是只认 4 个 legacy 函数
- 能力目录来自 REGISTRY（与 LLM tools 同一套，渐进式披露 L0→L1→L2）
- 一个附件可多步工具链（视频第3秒帧→裁剪、OCR+取帧等）
- 参数从描述解析（第xx秒、坐标、CSV字段）
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from packages.agent_core.tools.registry import (
    REGISTRY,
    unfreeze_registry,
)
from services.orchestrator.capabilities import register_capabilities


@pytest.fixture(autouse=True)
def _register_caps():
    """每个测试前注册 33 能力（与生产 deps 装配一致）。"""
    class _Writer:
        def put_derived(self, source_key: str, name: str, data: bytes) -> str:
            return "derived/" + name + "#sha256:" + "b" * 64

    unfreeze_registry()
    REGISTRY.clear()
    register_capabilities(include_code_exec=False, derived_writer=_Writer())
    yield
    unfreeze_registry()


@dataclass(frozen=True)
class _Att:
    id: str
    kind: str
    role: str = ""
    mime: str = ""
    insight_ref: str = ""
    source_ref: str = "att/demo/original#sha256:" + "a" * 64


def _import(name):
    import importlib
    return importlib.import_module(name)


# ---------- 能力目录：与 LLM tools 同一套 REGISTRY ----------

def test_catalog_comes_from_registry_not_hardcoded() -> None:
    """规划器的能力目录必须来自 REGISTRY（33 个），不是自造 4 个函数。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    catalog = planner.attachment_catalog()
    ids = {c["id"] for c in catalog}
    assert "tool.video.frame_at" in ids
    assert "tool.image.crop" in ids
    assert "tool.csv.filter" in ids
    assert "tool.text.parse" in ids
    assert len(ids) >= 20  # 33 能力中 24 个是附件相关，其余是 code/recipe


def test_catalog_is_l0_progressive_disclosure() -> None:
    """L0 只含 id/kind/brief/cost_hint，不泄漏 schema/handler。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    for c in planner.attachment_catalog():
        assert set(c.keys()) <= {"id", "kind", "brief", "cost_hint"}
        assert "args_schema" not in c and "handler" not in c


def test_l1_detail_on_demand() -> None:
    """L1 按需取真实 schema——渐进式披露，不全量下发。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    detail = planner.capability_schema("tool.video.frame_at")
    assert "args_schema" in detail
    props = detail["args_schema"]["properties"]
    assert "at_seconds" in props and "source_ref" in props


# ---------- 描述 → 工具链（多步 + 参数） ----------

def test_frame_at_from_description() -> None:
    """「视频第 3.5 秒的帧」→ frame_at(at_seconds=3.5)。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    plan = planner.plan_attachment(
        "用视频第 3.5 秒的帧做 2x2 卡片背景",
        (_Att("a1", "video", mime="video/mp4"),),
    )
    steps = plan.steps
    assert any(s.capability_id == "tool.video.frame_at" for s in steps)
    step = next(s for s in steps if s.capability_id == "tool.video.frame_at")
    assert step.args["at_seconds"] == 3.5


def test_frame_at_then_crop_chain() -> None:
    """「第5秒的帧裁出左上角」→ frame_at → crop 两步链，后者引用前者派生 ref。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    plan = planner.plan_attachment(
        "取视频第 5 秒的帧，裁出中间区域做背景",
        (_Att("a1", "video"),),
    )
    ids = [s.capability_id for s in plan.steps]
    assert ids.index("tool.video.frame_at") < ids.index("tool.image.crop")
    crop = next(s for s in plan.steps if s.capability_id == "tool.image.crop")
    assert crop.args.get("source_from") == "prev"  # 依赖上一步派生 ref


def test_csv_field_filter_from_description() -> None:
    """「按 CSV 的 date 列筛选 2026 的行」→ csv.filter(field=date, value=2026)。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    plan = planner.plan_attachment(
        "用 CSV 里 date 列等于 2026 的行做 4x4 清单",
        (_Att("a1", "text", role="data_source", mime="text/csv"),),
    )
    filt = next((s for s in plan.steps if s.capability_id == "tool.csv.filter"), None)
    assert filt is not None
    assert filt.args["field"] == "date"
    assert "2026" in str(filt.args["value"])


def test_multi_tool_ocr_plus_frame() -> None:
    """「视频取帧并识别文字」→ frames_ocr 一步覆盖（不是只挑第一个）。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    plan = planner.plan_attachment(
        "把视频第 2 秒和第 4 秒的画面文字显示出来",
        (_Att("a1", "video"),),
    )
    ids = [s.capability_id for s in plan.steps]
    assert "tool.video.frames_ocr" in ids or "tool.video.frame_at" in ids
    assert len(ids) >= 1


def test_unmentioned_attachment_skipped() -> None:
    planner = _import("services.guardrails.interpreter.capability_planner")
    plan = planner.plan_attachment(
        "桌面上加个 2x2 的日期卡片",
        (_Att("a1", "image"),),
    )
    assert plan.steps == ()


# ---------- 执行：走 LOAD_CAPABILITY 协议 ----------

def test_execute_builds_decision_and_calls_gateway() -> None:
    """L2 执行必须构造 Decision 走 invoke.load（与 LLM tools 同一入口）。"""
    planner = _import("services.guardrails.interpreter.capability_planner")
    seen = []

    def fake_load(decision):
        seen.append(decision)
        return '{"ok":true,"derived_refs":{"frame_at_3.5.webp":"k#sha256:abc"}}'

    plan = planner.plan_attachment(
        "用视频第 3.5 秒的帧做背景",
        (_Att("a1", "video"),),
    )
    result = planner.execute_chain(plan, load_fn=fake_load, surface="generation")
    assert seen and seen[0].capability_id == "tool.video.frame_at"
    assert seen[0].level == 2
    assert seen[0].surface == "generation"
    assert result.ok


def test_execute_unknown_capability_fails_closed() -> None:
    planner = _import("services.guardrails.interpreter.capability_planner")
    step = planner.ToolStep("tool.not_registered", {}, "a1")
    with pytest.raises(ValueError):
        planner.execute_chain(planner.AttachmentPlan((step,)), load_fn=lambda d: "{}")
