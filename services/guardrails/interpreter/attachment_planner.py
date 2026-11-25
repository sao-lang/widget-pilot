"""附件按需处理规划器（ADR-016 FR-10 扩展）。

用户上传附件后，不是上传时全量处理、也不是生成时忽略，而是**由描述决定**：
- 描述没提附件 → SKIP（不调任何工具）
- 只 vague 提到「带上这张图」→ LIGHT（元信息，不解码不 OCR）
- 明确要用内容（当数据源/当背景/提取文字/视频帧）→ FULL（调对应真实处理器）

真实工具由 services/control_plane/attachments/processor_adapter.py:RealProcessor 提供；
本模块只做「规划 + 执行编排」，不复制 OCR/Pillow/ffmpeg 逻辑。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class Depth:
    """处理深度（StrEnum 风格常量，避免额外依赖）。"""

    SKIP = "skip"
    LIGHT = "light"
    FULL = "full"


@dataclass(slots=True, frozen=True)
class PlanItem:
    attachment_id: str
    tool: str
    depth: str
    target: str  # palette | text | regions | rows | frames | meta
    reason: str
    attachment: object | None = None  # 原始附件引用，供工具解析字节
    insight_ref: str = ""  # 缓存键（内容 hash）


@dataclass(slots=True, frozen=True)
class AttachmentResult:
    slots: dict[str, object]
    missing_slots: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


# 描述 → 深度/工具的信号词（单一事实源，不散落）
_FULL_SIGNALS: tuple[tuple[str, str, str], ...] = (
    (r"当数据源|按这个表|这份数据|CSV|表格数据|按表中数值", "process_data_source", "rows"),
    (r"当背景|做底图|视觉参考|配色|学这个风格|风格参考", "process_visual", "palette"),
    (r"提取|读出|识别文字|OCR|画面里的|文字内容", "process_ocr", "text"),
    (r"视频里|逐帧|关键帧|第几秒", "process_video", "regions"),
)
_VAGUE = re.compile(r"这张图|这个视频|这个表|附件|带上|带上图|用了图")


class AttachmentPlanner:
    """根据描述 + 附件 role/kind 规划处理策略。"""

    def plan(self, description: str, attachments) -> tuple[PlanItem, ...]:
        if not attachments:
            return ()
        mentioned = bool(_VAGUE.search(description)) or any(
            re.search(p, description) for p, _, _ in _FULL_SIGNALS
        )
        if not mentioned:
            return tuple(
                PlanItem(getattr(a, "id", "?"), "", Depth.SKIP, "meta", "描述未引用附件",
                         attachment=a, insight_ref=getattr(a, "insight_ref", ""))
                for a in attachments
            )

        plans: list[PlanItem] = []
        for att in attachments:
            aid = getattr(att, "id", "?")
            kind = getattr(att, "kind", "")
            role = getattr(att, "role", "") or ""
            # 视频优先按 kind 分派（与 RealProcessor 一致：process_video）
            if kind == "video" and re.search(r"视频|逐帧|关键帧", description):
                plans.append(PlanItem(aid, "process_video", Depth.FULL, "regions", "视频内容",
                                      attachment=att, insight_ref=getattr(att, "insight_ref", "")))
                continue
            matched = next(
                ((tool, target) for pat, tool, target in _FULL_SIGNALS
                 if re.search(pat, description)),
                None,
            )
            if matched:
                tool, target = matched
                # kind 与工具冲突时以 kind 为准（CSV 必须走 data_source）
                if kind in ("text", "csv") and tool != "process_data_source":
                    tool, target = "process_data_source", "rows"
                plans.append(PlanItem(aid, tool, Depth.FULL, target, "描述明确要求内容",
                                      attachment=att, insight_ref=getattr(att, "insight_ref", "")))
            elif role:
                # 描述只泛泛提及，但附件 role 已定 → 按 role 决定工具（FULL）
                role_tool = {
                    "visual_reference": ("process_visual", "palette"),
                    "content_extraction": ("process_ocr", "text"),
                    "data_source": ("process_data_source", "rows"),
                    "style_reference": ("process_visual", "palette"),
                }.get(role)
                if role_tool:
                    plans.append(PlanItem(aid, role_tool[0], Depth.FULL, role_tool[1],
                                          f"附件 role={role}", attachment=att,
                                          insight_ref=getattr(att, "insight_ref", "")))
                else:
                    plans.append(PlanItem(aid, "", Depth.LIGHT, "meta", "role 未映射",
                                          attachment=att, insight_ref=getattr(att, "insight_ref", "")))
            else:
                plans.append(PlanItem(aid, "", Depth.LIGHT, "meta", "描述仅泛泛提及",
                                      attachment=att, insight_ref=getattr(att, "insight_ref", "")))
        return tuple(plans)


def execute(plan: tuple[PlanItem, ...], *, tools, cache: dict | None = None) -> AttachmentResult:
    """按 plan 调工具；失败/空数据降级 missing，不炸解释器。"""
    cache = cache if cache is not None else {}
    slots: dict[str, object] = {}
    missing: list[str] = []
    evidence: list[str] = []

    for item in plan:
        if item.depth == Depth.SKIP:
            continue
        cache_key = item.insight_ref or item.attachment_id
        cached = cache.get(cache_key)
        if cached is not None:
            slots.update(cached)
            evidence.append(f"cache@{item.attachment_id}")
            continue
        if item.depth == Depth.LIGHT:
            # 只用已知元信息，不解码字节
            slots["attachment_meta"] = item.attachment_id
            evidence.append(f"light@{item.attachment_id}")
            continue
        fn = getattr(tools, item.tool, None)
        if fn is None:
            logger.warning("附件工具 %s 不可用（attachment=%s）", item.tool, item.attachment_id)
            missing.append(item.target)
            continue
        try:
            result = fn(item.attachment if item.attachment is not None else item.attachment_id)
        except Exception as exc:  # noqa: BLE001 - 工具异常降级，不跳过判定
            logger.warning("附件工具 %s 失败：%s", item.tool, exc)
            missing.append(item.target)
            continue
        extracted = getattr(result, "extracted", result)
        if not isinstance(extracted, dict):
            missing.append(item.target)
            continue
        # 空数据 CSV（row_count=0）不生成空 widget
        if extracted.get("row_count") == 0:
            missing.append(item.target)
            continue
        value = extracted.get(item.target)
        if value is None or value == [] or value == "":
            missing.append(item.target)
            continue
        slots[item.target] = value
        evidence.append(f"{item.tool}@{item.attachment_id}")
    return AttachmentResult(slots=slots, missing_slots=tuple(missing),
                            evidence=tuple(evidence))


__all__ = ["AttachmentPlanner", "AttachmentResult", "Depth", "PlanItem", "execute"]
