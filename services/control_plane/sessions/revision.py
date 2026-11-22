"""ADR-010 §4 会话修正纯逻辑（撤销违法合并 / 改需求重跑语义）。

本模块只放确定性、零模型的纯函数与词表加载器：撤销意图识别、违法片段剥离、
两轮消息合并。生产源码无 Fake / Stub / type ignore，测试桩只在 tests/unit 内联。

词表与 ADR-010 §3 前置危险调用检测共用同一份
``services/guardrails/patterns/dangerous_actions.json``（单一事实来源），
并沿用 ``WIDGETPILOT_DANGEROUS_LEXICON_PATH`` 外置覆盖约定（非法即 fail-closed）。
合并结果本身不裁决放行——仍由上游校验链（V1）判定，命中违法则继续拒。
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = [
    "MergeResult",
    "RevisionPhrases",
    "default_revision_phrases",
    "detect_revocation_intent",
    "merge_revised_description",
    "strip_dangerous_segments",
]

_LEXICON = (
    Path(__file__).parents[2]
    / "guardrails"
    / "patterns"
    / "dangerous_actions.json"
)
_ENV_LEXICON = "WIDGETPILOT_DANGEROUS_LEXICON_PATH"
_LEXICON_KEYS = ("dangerous_action_phrases", "revocation_phrases")

# 中文常见句读：按这些标点切句，命中违法词的整句被剥离（确定性句式规则）。
_CLAUSE_SPLIT = re.compile(r"[，。！？；,;\n、]")
_JOINER = "，"


@dataclass(frozen=True, slots=True)
class RevisionPhrases:
    """合并所需的两组词表（来自 §3 同一词表文件）。"""

    dangerous_actions: tuple[str, ...]
    """违法动作短语：命中即定位为待剥离片段。"""
    revocations: tuple[str, ...]
    """撤销意图短语：命中表示用户在撤回前一轮请求。"""


@dataclass(frozen=True, slots=True)
class MergeResult:
    """T1 + T2 的确定性合并结果（供审计留证与新一轮 message 处理）。"""

    description: str
    """合并后的有效描述：T1 合法片段 + T2 意图。"""
    revoked: bool
    """T2 是否命中撤销意图。"""
    stripped_phrases: tuple[str, ...]
    """从 T1 剥离的违法词（按文本出现顺序，去重）。"""
    original: str
    """T1 原文（不删历史，仍留在 SessionRow.original_description）。"""
    followup: str
    """T2 原文（用户撤销/改合法意图）。"""


@lru_cache(maxsize=1)
def default_revision_phrases() -> RevisionPhrases:
    """加载进程级词表单例：内置词表 + 可选合规方外置覆盖。

    Returns:
        违法动作短语与撤销意图短语的不可变元组。

    Raises:
        ValueError: 外置覆盖不是对象或目标类别不是非空字符串数组（fail-closed）。
    """
    raw = json.loads(_LEXICON.read_text(encoding="utf-8"))
    override = os.getenv(_ENV_LEXICON, "")
    if override:
        external = json.loads(Path(override).read_text(encoding="utf-8"))
        if not isinstance(external, dict):
            raise ValueError("dangerous lexicon override must be an object")
        for key in _LEXICON_KEYS:
            if key in external:
                value = external[key]
                if not isinstance(value, list) or not all(
                    isinstance(item, str) and item for item in value
                ):
                    raise ValueError(f"dangerous lexicon.{key} must be string array")
                raw[key] = value
    return RevisionPhrases(
        dangerous_actions=tuple(_as_strings(raw["dangerous_action_phrases"])),
        revocations=tuple(_as_strings(raw["revocation_phrases"])),
    )


def _as_strings(values: Sequence[object]) -> Sequence[str]:
    """把 JSON 词表条目规范为非空字符串序列（空项与非法项丢弃）。"""
    return [v for v in values if isinstance(v, str) and v]


def detect_revocation_intent(text: str, revocation_phrases: Sequence[str]) -> bool:
    """判断文本是否表达"撤回前一轮违法请求"的意图。

    Args:
        text: T2 用户消息文本。
        revocation_phrases: 撤销意图词表（如"不进行/取消/去掉/改成合法"）。

    Returns:
        任一撤销短语命中即 True；纯子串匹配，零模型、零语义猜测。
    """
    return any(phrase and phrase in text for phrase in revocation_phrases)


def strip_dangerous_segments(
    text: str, dangerous_phrases: Sequence[str]
) -> tuple[str, tuple[str, ...]]:
    """按句读切句，剥离命中违法词的整句，返回残余合法片段与命中词。

    Args:
        text: T1 原文（含违法动作）。
        dangerous_phrases: 违法动作词表。

    Returns:
        ``(残余合法文本, 命中的违法词元组)``。残余为空串表示整条原文违法；
        命中词按文本出现顺序去重，供审计留证（不把违法原文写进合并结果）。
    """
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(text) if c.strip()]
    kept: list[str] = []
    hits: list[str] = []
    for clause in clauses:
        matched = [p for p in dangerous_phrases if p and p in clause]
        if matched:
            for phrase in matched:
                if phrase not in hits:
                    hits.append(phrase)
            continue
        kept.append(clause)
    return _JOINER.join(kept).strip(_JOINER), tuple(hits)


def merge_revised_description(
    original: str,
    followup: str,
    *,
    dangerous_phrases: Sequence[str],
    revocation_phrases: Sequence[str],
) -> MergeResult:
    """把 T1 原文与 T2 撤销意图合并为一条有效描述（确定性）。

    规则：识别 T2 撤销意图 → 剥离 T1 中违法词命中的整句 → 以连接词归一拼接
    T1 合法片段与 T2 意图；T1 全违法时退化为 T2；T2 为空时退化为 T1 合法片段。
    不覆盖 T1 原文（留 ``original`` 字段供调用方保留历史），合并后是否放行由
    上游校验链裁决——若合并结果仍含违法词则继续拒。

    Args:
        original: T1 原文（含违法动作，已存于 SessionRow.original_description）。
        followup: T2 用户消息（表达撤销/改成合法）。
        dangerous_phrases: 违法动作词表。
        revocation_phrases: 撤销意图词表。

    Returns:
        MergeResult：合并描述、撤销判定、剥离词留证、两轮原文。
    """
    safe, stripped = strip_dangerous_segments(original, dangerous_phrases)
    followup = followup.strip()
    revoked = detect_revocation_intent(followup, revocation_phrases)
    # T2 原文整体保留（其撤销措辞即"改成合法"的意图，去掉反而丢语义）。
    if safe and followup:
        description = f"{safe}{_JOINER}{followup}"
    else:
        description = safe or followup
    return MergeResult(
        description=description,
        revoked=revoked,
        stripped_phrases=stripped,
        original=original,
        followup=followup,
    )
