"""分类敏感词检测（ADR-005）：Aho-Corasick 多模式匹配 + 外置合规词库。

涉政词库刻意不内置；由合规方通过 ``WIDGETPILOT_SENSITIVE_LEXICON_PATH``
指向同结构 JSON（category -> 词数组）按类别覆盖。命中只留类别引用，
不把敏感词原文写进 guard_hit。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

import ahocorasick

from services.guardrails.types import GuardHitType, InputGuardHit

_LEXICON_PATH = Path(__file__).parent / "patterns" / "sensitive_lexicon.json"
_ENV_OVERRIDE = "WIDGETPILOT_SENSITIVE_LEXICON_PATH"

SENSITIVE_RULE_IDS: Mapping[str, str] = {
    "PORN": "SENS_PORN",
    "VIOLENCE": "SENS_VIOLENCE",
    "POLITICAL": "SENS_POLITICAL",
}


@dataclass(slots=True, frozen=True)
class SensitiveHit:
    """一条敏感词命中（只留类别与规则 id，不留原文）。"""

    category: str
    rule_id: str


def _load_lexicon() -> dict[str, tuple[str, ...]]:
    """加载内置词库并按类别覆盖合规方外置词库；外置文件非法即 fail-fast。"""
    raw = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        external = json.loads(Path(override).read_text(encoding="utf-8"))
        if not isinstance(external, dict):
            raise ValueError(
                f"sensitive lexicon override must be an object: {override}"
            )
        raw = {**raw, **external}
    lexicon: dict[str, tuple[str, ...]] = {}
    for category in SENSITIVE_RULE_IDS:
        words = raw.get(category, ())
        if not isinstance(words, list) or not all(
            isinstance(w, str) and w for w in words
        ):
            raise ValueError(f"invalid sensitive lexicon category: {category}")
        lexicon[category] = tuple(words)
    return lexicon


class SensitiveWordDetector:
    """基于成熟 Aho-Corasick 自动机的多模式敏感词检测器。"""

    def __init__(self, lexicon: Mapping[str, Sequence[str]]) -> None:
        automaton = ahocorasick.Automaton()
        for category, words in lexicon.items():
            for word in words:
                automaton.add_word(
                    word, (category, SENSITIVE_RULE_IDS.get(category, "SENS_UNKNOWN"))
                )
        automaton.make_automaton()
        self._automaton = automaton

    def scan(self, text: str) -> SensitiveHit | None:
        """返回首个命中（按文本顺序）；无命中返回 None。"""
        for _end, value in self._automaton.iter(text):
            category, rule_id = cast(tuple[str, str], value)
            return SensitiveHit(category=category, rule_id=rule_id)
        return None

    def to_guard_hit(self, hit: SensitiveHit) -> InputGuardHit:
        """转换为进护栏留证；excerpt 只写类别，不泄漏敏感词原文。"""
        return InputGuardHit(
            hit_type=GuardHitType.SENSITIVE,
            rule_id=hit.rule_id,
            judgement=f"敏感内容命中：{hit.category}（词库分类检测）",
            excerpt=f"<sensitive:{hit.category}>",
        )


@lru_cache(maxsize=1)
def default_sensitive_detector() -> SensitiveWordDetector:
    """进程级单例：内置词库 + 可选合规方外置词库。"""
    return SensitiveWordDetector(_load_lexicon())
