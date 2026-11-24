"""语义层校验（§3.2 step2-6）：描述充分性 / role 明确性 / 规格获取与冲突。

不足 / 冲突 / 规格缺失必给具体建议，不静默放行；不推断未说明的 grid（ADR-001 D-6）。
"""

from __future__ import annotations

import re

from packages.contracts.enums import GridSpec, ReasonCode
from services.guardrails.constants import (
    FEATURE_HINTS,
    GRID_SYNONYMS,
    MIN_DESCRIPTION_LEN,
    ROLE_CLARIFY_THRESHOLD,
    WIDGET_ANCHOR_PATTERNS,
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
_DESC_HINT = "请说明想在桌面/锁屏展示什么内容，以及需要哪些关键特征"


def _has_required_slots(description: str) -> bool:
    """R1：描述须含「宿主/产物锚点 或 开放特征词」至少一类槽位（ADR-004 去类型化）。

    锚点回答「放在哪里/以什么形态承载」（桌面/锁屏/小组件/卡片/日历等），
    特征词回答「展示什么数据」（天气/快递/股票/步数等开放词族）。两者都不
    再映射到 WidgetType；泛指词（玩意/小工具/东西）仍不能单独满足槽位。
    """
    has_anchor = any(
        re.search(pattern, description, flags=re.IGNORECASE)
        for pattern in WIDGET_ANCHOR_PATTERNS
    )
    has_feature = any(
        re.search(pattern, description, flags=re.IGNORECASE)
        for pattern in FEATURE_HINTS
    )
    return has_anchor or has_feature


def _detect_feature(description: str) -> str:
    """抽取规格硬冲突特征词（如「月视图」），供放行阶段冲突矩阵判定。

    别名取自共用的 GRID_SYNONYMS（与 grid_resolver 同源，避免词表漂移）。
    「复杂面板」是密度软提示、不在硬冲突矩阵内， suggest_for 查不到即不拦截。
    """
    for feature, words in GRID_SYNONYMS.items():
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
