"""ADR-009 多轮对话：零 LLM 结构化事实摘要（v0.2 A 方案）。

把一轮的 ``State.spec`` + 用户修改意图抽成固定的短事实（白名单字段），用于
下一轮装配到结论槽尾部——不回放原始对话消息，不调用模型（I-M2），不碰 1–5 槽
caching 前缀。字段上限按 ADR-009 O1（8 任务 / 5 特征）影子校准初值。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from packages.agent_core.types import JsonValue, State

MAX_FACT_TASKS: int = 8
"""同会话进摘要的最大任务数（O1 影子校准初值）。"""
MAX_FACT_FEATURES: int = 5
"""每任务进摘要的最大关键特征数（O1）。"""
MAX_INTENT_CHARS: int = 120
"""last_intent 截断长度（短事实，不存对话原文）。"""

# 已过 M06 门禁，但事实会在下一轮再进 prompt——这里二次过滤形状命中（纵深防御）。
_PHONE_RE: re.Pattern[str] = re.compile(r"1[3-9]\d{9}")
_EMAIL_RE: re.Pattern[str] = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_INJECTION_RE: re.Pattern[str] = re.compile(
    r"忽略(?:之前|上述|前面|先前)的?(?:指令|指示|提示)"
    r"|ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)",
    re.IGNORECASE,
)
_REDACTED = "[已过滤]"


@dataclass(frozen=True)
class FactTask:
    """单个任务的结构化事实（确定性，字段白名单）。"""

    widget_type: str
    grid: str | None
    palette: str | None
    key_features: tuple[str, ...]
    last_intent: str
    turn_count: int


@dataclass(frozen=True)
class FactSummary:
    """会话级事实摘要（零 LLM，可确定性复算）。"""

    session_id: str
    tasks: tuple[FactTask, ...] = ()
    turn_index: int = 1

    @property
    def turn_count(self) -> int:
        """当前 turn 序号（与 turn_index 同义，供测试/审计读取）。"""
        return self.turn_index


def _sanitize_intent(text: str) -> str:
    """过滤 PII 形状与注入串后截断（M06 已过门禁，此处纵深防御）。"""
    cleaned = _PHONE_RE.sub(_REDACTED, text)
    cleaned = _EMAIL_RE.sub(_REDACTED, cleaned)
    cleaned = _INJECTION_RE.sub(_REDACTED, cleaned)
    return cleaned.strip()[:MAX_INTENT_CHARS]


def _as_short_str(value: JsonValue | None) -> str | None:
    """把 JSON 边界值收窄为短字符串（非字符串回退 None）。"""
    return value if isinstance(value, str) and value else None


def build_fact_summary(
    session_id: str,
    state: State,
    *,
    turn_index: int,
    last_intent: str,
    widget_type: str = "custom",
    key_features: list[str] | None = None,
) -> FactSummary:
    """从 State.spec + 输入字段纯代码抽取 FactSummary（零模型，同输入同输出）。

    Args:
        session_id: 会话标识（租户锚点）。
        state: 本轮终态 State（只读 spec）。
        turn_index: 本轮迭代序号（1-based）。
        last_intent: 用户本轮修改意图（须已过 M06 门禁；此处二次过滤）。
        widget_type: 归一化控件类型 hint（来自 Input，State 不承载此字段）。
        key_features: 可选关键特征列表，超上限截断（白名单短字符串）。

    Returns:
        可确定性复算的 FactSummary。
    """
    spec = state.spec
    extras = spec.extras
    palette = _as_short_str(extras.get("palette"))
    features = tuple(
        f for f in (key_features or [])[:MAX_FACT_FEATURES] if isinstance(f, str) and f
    )
    task = FactTask(
        widget_type=widget_type or "custom",
        grid=spec.grid,
        palette=palette,
        key_features=features,
        last_intent=_sanitize_intent(last_intent),
        turn_count=turn_index,
    )
    return FactSummary(
        session_id=session_id,
        tasks=(task,)[:MAX_FACT_TASKS],
        turn_index=turn_index,
    )


def render_fact_summary(fact: FactSummary | None) -> str:
    """渲染 FactSummary 为结论槽尾部的短文本（空事实返回空串）。"""
    if fact is None:
        return ""
    lines: list[str] = []
    for task in fact.tasks[:MAX_FACT_TASKS]:
        parts = [f"widget={task.widget_type}"]
        if task.grid:
            parts.append(f"grid={task.grid}")
        if task.palette:
            parts.append(f"palette={task.palette}")
        if task.key_features:
            parts.append("features=" + "/".join(task.key_features[:MAX_FACT_FEATURES]))
        if task.last_intent:
            parts.append(f"last_intent={task.last_intent}")
        lines.append("- " + " ".join(parts) + f" (turn={task.turn_count})")
    return "历史事实：\n" + "\n".join(lines) if lines else ""
