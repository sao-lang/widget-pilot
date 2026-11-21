"""M07 记忆 Store → 上下文装配端口适配器（v1.6）。

把 PG 持久化 Store（``PreferenceStorePort`` / ``ProcedureStorePort``）适配成
上下文装配侧的读取端口（``PreferencePort`` / ``ProcedurePort``），执行 D-11
高置信门禁与衰减排序、D-14 程序记忆采纳阈值。装配期只读取，不写库。
"""
from __future__ import annotations

from datetime import UTC, datetime

from services.orchestrator.memory.learning import (
    is_high_confidence,
    rank_evidence,
    should_inject_procedure,
)
from services.orchestrator.memory.ports import (
    PreferenceStorePort,
    ProcedureStorePort,
)
from services.orchestrator.memory.types import (
    PreferenceField,
    UserPreference,
)


def high_confidence_grids(pref: UserPreference) -> list[str]:
    """从偏好中提取高置信 preferred_grids（D-11 门禁，供 B1 校验链注入）。

    Args:
        pref: 用户偏好聚合（每字段带证据）。

    Returns:
        高置信规格字符串列表；无高置信证据返回空列表。
    """
    evidence = pref.fields.get(PreferenceField.PREFERRED_GRIDS)
    if evidence is None or not is_high_confidence(evidence):
        return []
    value = evidence.value
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class PreferenceInjectionAdapter:
    """第 5 槽用户记忆读取适配器（实现 M03 侧 ``PreferencePort``）。

    Attributes:
        _store: 偏好持久化 Store（真实为 PgPreferenceStore）。
        _clock_iso: 注入的当前时刻 ISO 串；None 用系统 UTC（不读系统时钟由调用方保证）。
    """

    def __init__(
        self, store: PreferenceStorePort, clock_iso: str | None = None
    ) -> None:
        """构造适配器，注入 Store 与可选时钟。"""
        self._store = store
        self._clock_iso = clock_iso

    def injection_text(self, user_id: str, max_tokens: int) -> str:
        """生成高置信、衰减排序后的偏好注入文本（超预算按权重降序截断）。"""
        pref = self._store.get(user_id)
        ranked = rank_evidence(list(pref.fields.items()), now=self._clock_iso)
        lines: list[str] = []
        used = 0
        for field_name, evidence in ranked:
            # 每行估算 token（4 字符≈1 token），超预算即停（契约槽不被挤占）。
            line = f"- {field_name.value}: {evidence.value} (来源:{evidence.source.value})"
            cost = len(line) // 4 + 1
            if used + cost > max_tokens:
                break
            lines.append(line)
            used += cost
        return "\n".join(lines)


class ProcedureHintAdapter:
    """第 2 槽程序记忆软提示适配器（实现 M03 侧 ``ProcedurePort``）。

    Attributes:
        _store: 程序记忆持久化 Store（真实为 PgProcedureStore）。
    """

    def __init__(self, store: ProcedureStorePort) -> None:
        """构造适配器，注入程序记忆 Store。"""
        self._store = store

    def hint_text(self, user_id: str, widget_type: str) -> str:
        """采纳次数达阈值才返回软提示（D-14，不改写规程资产）。"""
        records = self._store.get_for_type(user_id, widget_type)
        hits = [r for r in records if should_inject_procedure(r.adopt_count)]
        if not hits:
            return ""
        best = max(hits, key=lambda r: (r.adopt_count, r.last_adopted_at))
        return f"用户多次采用规程 {best.recipe_id}（{best.adopt_count} 次），可优先考虑。"


def utc_now_iso() -> str:
    """当前 UTC 时刻 ISO 串（装配期默认时钟，秒级）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")

