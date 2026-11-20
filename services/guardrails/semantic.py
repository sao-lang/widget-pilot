"""语义层校验（§3.2 step2-6）：描述充分性 / role 明确性 / 规格获取与冲突。

不足 / 冲突 / 规格缺失必给具体建议，不静默放行；不推断未说明的 grid（ADR-001 D-6）。
"""

from __future__ import annotations

import re

from packages.contracts.enums import GridSpec, ReasonCode
from services.guardrails.constants import (
    FEATURE_HINTS,
    MIN_DESCRIPTION_LEN,
    ROLE_CLARIFY_THRESHOLD,
    WIDGET_HINTS,
)
from services.guardrails.grid_resolver import resolve_grid
from services.guardrails.spec_conflicts import suggest_for
from services.guardrails.types import (
    AttachmentView,
    GridStatus,
    SemanticResult,
    SpecConflict,
    SpecMissing,
)

_ROLE_HINT = "请说明这张图是当背景、提取内容、作为数据源还是风格参考"
_DESC_HINT = "请说明要日历还是时钟，以及是否需要翻月等关键特征"


def _has_required_slots(description: str) -> bool:
    """R1：描述须含「控件类型 或 关键特征」至少一类槽位（§3.2 step2，零模型调用）。

    设计原文写「控件类型 + 关键特征」，但对长尾描述（如「给我整一个能显示日期的
    小玩意」）没有显式控件词却有明确特征——这类正是 L2/L3 级联要接管的，不能在
    语义层被「描述不足」挡掉。故判定口径为「两类槽位至少命中一类即可生成」：
    控件类型仅认 **明确控件词**（WIDGET_HINTS：日历/时钟/闹钟/待办），
    关键特征来自 FEATURE_HINTS（翻月/计时/提醒/清单/显示等，纯外观词不算）。
    泛指词（玩意/小工具/东西）不能单独满足控件槽——否则「有样式的玩意」
    这类无信息描述同样放行，R1 形同虚设（review S3）。
    两类皆无（如「随便做一个东西」）才不足，给具体补充建议。
    """
    has_widget = any(
        re.search(pattern, description, flags=re.IGNORECASE)
        for pattern, _widget in WIDGET_HINTS
    )
    has_feature = any(
        re.search(pattern, description, flags=re.IGNORECASE)
        for pattern in FEATURE_HINTS
    )
    return has_widget or has_feature


def _detect_feature(description: str) -> str:
    """抽取规格冲突特征词（如「月视图」），供冲突矩阵判定。

    同义词归并：「翻月」「能翻月」映射为冲突矩阵的「月视图」特征。
    """
    synonyms = {
        "月视图": ("月视图", "翻月"),
        "多行列表": ("多行列表", "列表"),
        "宽表格": ("宽表格", "表格"),
        "复杂面板": ("复杂面板", "面板"),
    }
    for feature, words in synonyms.items():
        if any(word in description for word in words):
            return feature
    return ""


def validate_semantic(
    description: str,
    attachments: list[AttachmentView],
    grid: GridSpec | None,
    preferred_grids: list[str] | None = None,
    grid_confirmed: bool = False,
) -> SemanticResult:
    """语义层四步校验，返回首个失败原因。

    Args:
        description: 用户描述。
        attachments: M05 附件视图列表（role / confidence）。
        grid: 请求选择器规格（可选）。
        preferred_grids: 用户偏好规格（v1.4，只影响建议顺序）。
        grid_confirmed: 澄清态标记（B1，v1.8）——用户已就「规格缺失」作答，
            规格已由 answered grid 确定；此时跳过 step2 描述充分性复检，
            否则用户仅答 "4x4" 会再次因描述不足被拒，触发澄清死循环。
            仅跳过描述充分性，role 明确性/规格冲突等其余判定照常执行。

    Returns:
        SemanticResult；ok=False 时 reason_code 与具体建议非空。
    """
    # step2：描述充分性（空/过短/缺「控件类型 + 关键特征」槽位 → 不足，给具体建议）
    # B1（v1.8）：澄清态跳过——规格已由用户回答确定，描述不再复检，防止
    # 「答 4x4→描述不足→再挂起」死循环（clarify_count 2 次后 422 卡死）。
    if not grid_confirmed:
        if len(description.strip()) < MIN_DESCRIPTION_LEN:
            return SemanticResult(
                ok=False,
                reason_code=ReasonCode.INPUT_INVALID,
                suggestions=(_DESC_HINT,),
            )
        if not _has_required_slots(description):
            return SemanticResult(
                ok=False,
                reason_code=ReasonCode.INPUT_INVALID,
                suggestions=(_DESC_HINT,),
            )

    # step4：附件 role 明确性（不猜测，不默认任何 role）
    for att in attachments:
        if att.role is None or att.role_confidence < ROLE_CLARIFY_THRESHOLD:
            return SemanticResult(
                ok=False,
                reason_code=ReasonCode.INPUT_INVALID,
                suggestions=(_ROLE_HINT,),
            )

    # step3：规格获取三级来源
    resolution = resolve_grid(grid, description, preferred_grids)
    if resolution.status is GridStatus.MISSING:
        return SemanticResult(
            ok=False,
            reason_code=ReasonCode.SPEC_MISSING,
            suggestions=("请选择或告知规格（如 4x4）",),
            spec_missing=SpecMissing(suggest_grid=resolution.suggest_grid),
        )
    assert resolution.grid is not None  # selector/explicit_text 必带 grid

    # step6：规格冲突前置（spec 生成前提示改选）
    feature = _detect_feature(description)
    suggestion = suggest_for(resolution.grid, feature)
    if suggestion is not None:
        return SemanticResult(
            ok=False,
            reason_code=ReasonCode.SPEC_CONFLICT,
            suggestions=(
                f"{resolution.grid.value} 无法承载{feature}，建议改选 {suggestion.value}",
            ),
            spec_conflict=SpecConflict(
                grid=resolution.grid,
                feature=feature,
                suggest_grid=suggestion,
            ),
        )

    return SemanticResult(
        ok=True,
        grid=resolution.grid,
        grid_source=resolution.grid_source,
    )
