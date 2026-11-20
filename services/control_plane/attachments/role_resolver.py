"""M05 role 判定：描述关键词 → AttachmentRole 的纯规则 registry（§3.4 B）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RoleDecision:
    """role 判定结论（§4.3 RoleDecision）。"""

    role: str | None
    role_confidence: float
    matched_rule: str
    excerpt: str


# 结构性字面量：role 关键词规则表集中一处（§3.4 B，纯代码不改模型）
# 每条规则 = (role, 关键词元组, 命中置信度)；多条命中取最高分。
_RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("visual_reference", ("当背景", "做底图", "当底图", "背景图", "视觉参考"), 0.95),
    (
        "content_extraction",
        ("提取", "读出", "识别文字", "右上角日期", "画面里的日期", "OCR", "文字内容"),
        0.9,
    ),
    (
        "data_source",
        ("按这个表", "这份数据", "数据源", "按表中数值", "CSV", "表格数据"),
        0.9,
    ),
    (
        "style_reference",
        ("配色", "学这个风格", "风格参考", "样式参考", "圆角风格"),
        0.85,
    ),
)


def resolve_role(description: str, kind: str) -> RoleDecision:
    """纯规则匹配描述 → role + role_confidence；歧义返回 role=None。

    Args:
        description: 用户描述（v1.7 B1：上传时必填，role 判定唯一输入）。
        kind: 附件类别，仅用于未来扩展（当前规则与类别无关）。

    Returns:
        RoleDecision；命中取最高置信度，全不命中返回 role=None + confidence=0。
    """
    # 多规则命中取最高分：确定性，不依赖规则表顺序之外的任何状态。
    best_role: str | None = None
    best_score = 0.0
    best_rule = ""
    best_excerpt = ""
    for role, keywords, score in _RULES:
        for keyword in keywords:
            if keyword in description:
                if score > best_score:
                    best_role = role
                    best_score = score
                    best_rule = keyword
                    best_excerpt = description
                break  # 同一 role 多条关键词命中只计一次
    return RoleDecision(best_role, best_score, best_rule, best_excerpt)
