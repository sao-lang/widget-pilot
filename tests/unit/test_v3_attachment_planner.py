"""v3 附件按需处理规划器 TDD（ADR-016 FR-10 扩展）。

用户诉求：上传附件后，根据描述判断「要处理到什么程度、调用什么工具」，
而不是上传时无脑全量处理，也不是生成时完全忽略附件。

三类处理深度：
- SKIP：描述没用到这个附件 → 不调任何工具
- LIGHT：只要元信息（尺寸/格式/行数）→ 不解码不 OCR
- FULL：要内容（主色/OCR 文本/视频帧/CSV 行）→ 调对应真实处理器
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _FakeAttachment:
    id: str
    role: str = ""
    kind: str = "image"
    mime: str = "image/png"
    insight_ref: str = ""
    extracted: dict | None = None


def _import(name: str):
    import importlib
    return importlib.import_module(name)


# ---------- 规划：描述 → 处理策略 ----------

def test_plan_returns_tool_and_depth() -> None:
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan = planner.AttachmentPlanner().plan(
        "桌面上加个 2x2 的卡片，用我传的 CSV 当数据源",
        (_FakeAttachment("a1", role="data_source", kind="text", mime="text/csv"),),
    )
    assert plan[0].tool == "process_data_source"
    assert plan[0].depth == planner.Depth.FULL


def test_plan_visual_reference_uses_visual_tool() -> None:
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan = planner.AttachmentPlanner().plan(
        "把我传的截图当背景，做个 2x2 的桌面时钟",
        (_FakeAttachment("a1", role="visual_reference", kind="image"),),
    )
    assert plan[0].tool == "process_visual"
    assert plan[0].depth == planner.Depth.FULL


def test_plan_video_ocr_uses_video_tool_not_visual() -> None:
    """视频+提取文字 → process_video（逐帧 regions），不是关键帧当风格。"""
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan = planner.AttachmentPlanner().plan(
        "把视频里出现的时间文字显示在 2x2 卡片上",
        (_FakeAttachment("a1", role="content_extraction", kind="video", mime="video/mp4"),),
    )
    assert plan[0].tool == "process_video"
    assert plan[0].target == "regions"


def test_plan_light_depth_for_vague_mention() -> None:
    """只说「带这张图」没说怎么用 → LIGHT，不解码。"""
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan = planner.AttachmentPlanner().plan(
        "做个 2x2 的日期卡片，顺便带上这张图",
        (_FakeAttachment("a1", kind="image"),),
    )
    assert plan[0].depth == planner.Depth.LIGHT


def test_plan_skip_when_attachment_not_referenced() -> None:
    """描述完全没提附件 → SKIP，不调工具。"""
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan = planner.AttachmentPlanner().plan(
        "桌面上加个 2x2 显示今天日期的卡片",
        (_FakeAttachment("a1", role="data_source", kind="text"),),
    )
    assert plan[0].depth == planner.Depth.SKIP


# ---------- 执行：按 plan 调工具，产出结构化槽位 ----------

def test_executor_calls_only_planned_tool() -> None:
    planner = _import("services.guardrails.interpreter.attachment_planner")
    calls: list[str] = []

    class _Tools:
        def process_data_source(self, att):
            calls.append("data")
            return {"format": "csv", "rows": [["x", "1"]], "row_count": 1}

        def process_visual(self, att):
            calls.append("visual")
            return {"palette": ["#fff"]}

    plan = planner.AttachmentPlanner().plan(
        "用我传的 CSV 当数据源，做个 2x2 卡片",
        (_FakeAttachment("a1", role="data_source", kind="text"),),
    )
    result = planner.execute(plan, tools=_Tools())
    assert calls == ["data"], calls
    assert "rows" in result.slots


def test_executor_caches_by_insight_ref() -> None:
    """同一 insight_ref 不重复处理（幂等，省工具调用）。"""
    planner = _import("services.guardrails.interpreter.attachment_planner")
    calls: list[str] = []

    class _Tools:
        def process_visual(self, att):
            calls.append("visual")
            return {"palette": ["#fff"]}

    att = _FakeAttachment("a1", role="visual_reference", kind="image",
                          insight_ref="k#sha256:abc")
    plan = planner.AttachmentPlanner().plan("把这张图当背景，做个 2x2 卡片", (att,))
    first = planner.execute(plan, tools=_Tools(), cache={})
    second = planner.execute(plan, tools=_Tools(), cache={"k#sha256:abc": first.slots})
    assert calls == ["visual"]  # 第二次命中缓存
    assert second.slots == first.slots


def test_executor_empty_csv_is_missing_not_filled() -> None:
    """CSV 只有表头没数据 → 对应槽位 missing，不生成空 widget。"""
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan_item = planner.PlanItem(attachment_id="a1", tool="process_data_source",
                                 depth=planner.Depth.FULL, target="rows", reason="数据源")

    class _Tools:
        def process_data_source(self, att):
            return {"format": "csv", "columns": ["a", "b"], "rows": [], "row_count": 0}

    result = planner.execute((plan_item,), tools=_Tools())
    assert result.missing_slots


def test_executor_failed_tool_degrades_not_crashes() -> None:
    """工具超时/异常 → 降级 missing，不炸解释器。"""
    planner = _import("services.guardrails.interpreter.attachment_planner")
    plan_item = planner.PlanItem(attachment_id="a1", tool="process_ocr",
                                 depth=planner.Depth.FULL, target="text", reason="提取文字")

    class _Tools:
        def process_ocr(self, att):
            raise TimeoutError("ocr timeout")

    result = planner.execute((plan_item,), tools=_Tools())
    assert result.missing_slots
