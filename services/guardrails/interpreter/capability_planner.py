"""能力驱动的附件规划器（ADR-014 33 能力 + ADR-016）。

与 LLM tools 共用 REGISTRY（渐进式披露 L0 摘要 → L1 schema → L2 执行），
根据描述把附件处理规划成**多步工具链**，参数从描述解析：
- 「视频第 3.5 秒的帧做背景」→ tool.video.frame_at(at_seconds=3.5)
- 「第 5 秒帧裁出中间」→ frame_at → crop（source_from=prev 依赖派生 ref）
- 「CSV 的 date 列等于 2026」→ tool.csv.filter(field=date, value=2026)
- 「第 2、4 秒画面文字」→ tool.video.frames_ocr(times_s=(2,4))

不自己实现工具——只做「规划 + 构造 Decision + 走 invoke.load」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from packages.agent_core.tools.registry import REGISTRY, capability_detail, level0
from packages.agent_core.types import Decision, DecisionKind
from services.guardrails.constants import INTENT_GUARD_PATTERNS

# 附件相关能力前缀（与 REGISTRY 注册 id 单一事实源）
_ATTACHMENT_PREFIXES = ("tool.image.", "tool.video.", "tool.text.", "tool.csv.",
                        "tool.attachment.", "vision.")


def attachment_catalog() -> tuple[dict, ...]:
    """L0 目录：只含 id/kind/brief/cost_hint（渐进式披露，不泄漏 schema）。"""
    return tuple(
        c for c in level0() if str(c.get("id", "")).startswith(_ATTACHMENT_PREFIXES)
    )


def capability_schema(capability_id: str) -> dict:
    """L1 按需取 schema（不执行）；未注册抛 ValueError（fail-closed）。"""
    if capability_id not in REGISTRY:
        raise ValueError(f"unregistered capability: {capability_id}")
    return capability_detail(capability_id)


@dataclass(slots=True, frozen=True)
class ToolStep:
    capability_id: str
    args: dict
    attachment_id: str
    source_from: str = ""  # "" = 原始 source_ref；"prev" = 上一步派生 ref


@dataclass(slots=True, frozen=True)
class AttachmentPlan:
    steps: tuple[ToolStep, ...]
    reason: str = ""


@dataclass(slots=True, frozen=True)
class ChainResult:
    ok: bool
    outputs: tuple[str, ...] = ()
    reason: str = ""


# 描述信号 → 能力 id（单一事实源，按优先级排列）
_SIGNALS: tuple[tuple[str, str], ...] = (
    (r"第\s*\d+(?:\.\d+)?\s*秒.{0,8}(?:文字|OCR|识别)", "tool.video.frames_ocr"),
    (r"(?:视频|画面).{0,8}(?:文字|OCR|识别)", "tool.video.frames_ocr"),
    (r"第\s*\d+(?:\.\d+)?\s*秒.{0,10}(?:帧|画面|截图)", "tool.video.frame_at"),
    (r"关键帧|等间隔抽帧", "tool.video.keyframes"),
    (r"(?:时长|分辨率|帧率|多长)", "tool.video.probe"),
    (r"裁剪|裁出|截取.{0,6}区域|左上角|右上角|中间区域", "tool.image.crop"),
    (r"定位|找出.{0,8}位置|在哪", "tool.vision.locate"),
    (r"(?:多少|统计|汇总|求和|平均).{0,8}(?:行|列|数据)", "tool.csv.stats"),
    (r"[\w]+\s*列.{0,8}(?:等于|为|=)|(?:按|筛选|过滤).{0,8}列", "tool.csv.filter"),
    (r"(?:排序|按.{0,6}排)", "tool.csv.sort"),
    (r"(?:只要|选择|取).{0,8}列", "tool.csv.select"),
    (r"(?:CSV|表格|这份数据|数据源)", "tool.csv.parse"),
    (r"(?:识别|读出|OCR|提取).{0,6}文字", "tool.image.ocr"),
    (r"(?:当背景|做底图|视觉参考|配色|风格参考|背景图)", "tool.image.inspect"),
    (r"(?:文本|txt|文档).{0,6}(?:统计|字数|行数)", "tool.text.stats"),
    (r"(?:文本|txt|文档).{0,6}(?:清洗|规范化|去空行)", "tool.text.clean"),
    (r"(?:文本|txt|文档).{0,6}(?:分块|切分)", "tool.text.chunk"),
)
_VAGUE = re.compile(r"这张图|这个视频|这个表|附件|带上|带上图|用了图")
_SEC = re.compile(r"第\s*(\d+(?:\.\d+)?)\s*秒")
_SEC_LIST = re.compile(r"(\d+(?:\.\d+)?)\s*秒")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*列")
_EQUALS = re.compile(r"(?:等于|为|=)\s*([^\s，,。；;]+)")


def _source_ref(att) -> str:
    return getattr(att, "source_ref", "") or getattr(att, "insight_ref", "")


def _parse_args(capability_id: str, description: str, att, source_from: str) -> dict:
    args: dict = {}
    if source_from:
        args["source_from"] = source_from
    else:
        ref = _source_ref(att)
        if ref:
            args["source_ref"] = ref
    if capability_id in ("tool.video.frame_at",):
        m = _SEC.search(description)
        if m:
            args["at_seconds"] = float(m.group(1))
        # 不传 format 默认值：既有 _make_media_handler arg_names 含 format，
        # 但 frame_at() 不接该参数（仓库既有缺陷），省略让 args_model 默认值生效。
    if capability_id == "tool.video.frames_ocr":
        times = tuple(float(x) for x in _SEC_LIST.findall(description))
        if times:
            args["times_s"] = times
    if capability_id == "tool.csv.filter":
        field = _FIELD.search(description)
        value = _EQUALS.search(description)
        if field:
            args["field"] = field.group(1)
        if value:
            args["value"] = value.group(1)
    if capability_id == "tool.image.crop":
        # 无精确 bbox 时由 vision.locate 后续补；此处只标记依赖
        args["source_from"] = "prev"
    return args


def plan_attachment(description: str, attachments) -> AttachmentPlan:
    """描述 + 附件 → 多步工具链。未提及附件 → 空计划（SKIP）。"""
    if not attachments:
        return AttachmentPlan(())
    mentioned = bool(_VAGUE.search(description)) or any(
        re.search(p, description) for p, _ in _SIGNALS
    )
    if not mentioned or any(re.search(p, description) for p in INTENT_GUARD_PATTERNS):
        return AttachmentPlan(())

    steps: list[ToolStep] = []
    for att in attachments:
        aid = getattr(att, "id", "?")
        kind = getattr(att, "kind", "")
        matched = [cid for pat, cid in _SIGNALS if re.search(pat, description)]
        # 按附件 kind 过滤工具族：图片不该调 video.*，文本不该调 image.*
        kind_allowed = {
            "video": ("tool.video.", "tool.image.", "vision."),
            "image": ("tool.image.", "vision."),
            "text": ("tool.text.", "tool.csv."),
            "csv": ("tool.csv.", "tool.text."),
        }.get(kind, ("tool.image.", "tool.video.", "tool.text.", "tool.csv.", "vision."))
        matched = [cid for cid in matched if cid.startswith(kind_allowed)]
        if not matched:
            continue
        # 去重保序；frame_at + crop 组合时 crop 依赖上一步
        seen: set[str] = set()
        for cid in matched:
            if cid in seen or cid not in REGISTRY:
                continue
            seen.add(cid)
            source_from = "prev" if cid == "tool.image.crop" and steps else ""
            steps.append(ToolStep(cid, _parse_args(cid, description, att, source_from), aid,
                                  source_from))
    return AttachmentPlan(tuple(steps), reason="描述触发能力链")


def execute_chain(plan: AttachmentPlan, *, load_fn, surface: str = "generation") -> ChainResult:
    """L2 执行：构造 Decision 走 invoke.load（与 LLM tools 同一入口）。"""
    outputs: list[str] = []
    prev_ref = ""
    for step in plan.steps:
        if step.capability_id not in REGISTRY:
            raise ValueError(f"unregistered capability: {step.capability_id}")
        args = dict(step.args)
        if args.pop("source_from", "") == "prev" and prev_ref:
            args["source_ref"] = prev_ref
        decision = Decision(
            kind=DecisionKind.LOAD_CAPABILITY,
            capability_id=step.capability_id,
            level=2,
            args=args,
            surface=surface,
            messages=(),  # type: ignore[arg-type] - 执行链不需模型消息
        )
        result = load_fn(decision)
        text = result if isinstance(result, str) else str(result)
        outputs.append(text)
        # 从上一步输出提取派生 ref（形如 k#sha256:...），供下一步引用
        m = re.search(r"([\w/.-]+#sha256:[0-9a-f]{64})", text)
        if m:
            prev_ref = m.group(1)
    return ChainResult(ok=True, outputs=tuple(outputs))


__all__ = [
    "AttachmentPlan",
    "ChainResult",
    "ToolStep",
    "attachment_catalog",
    "capability_schema",
    "execute_chain",
    "plan_attachment",
]
