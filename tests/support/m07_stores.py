"""测试替身（test doubles）：M07 记忆端口的内存实现，生产源码禁止 import。

与 PG 实现共用领域规则（``decide_operation`` / ``SOURCE_WEIGHT`` / ``utc_now_iso``），
只把持久化换成 dict/list，供装配层测试显式注入；hash 链只由真 PG 实现验证。
"""
from __future__ import annotations

from dataclasses import replace
from typing import cast

from packages.agent_core.types import JsonValue
from services.orchestrator.memory.learning import decide_operation
from services.orchestrator.memory.types import (
    SOURCE_WEIGHT,
    FieldEvidence,
    PreferenceDecision,
    PreferenceEvent,
    PreferenceField,
    PreferenceSource,
    UserPreference,
    UserProcedure,
    utc_now_iso,
)

__all__ = ["InMemoryPreferenceStore", "InMemoryProcedureStore"]


class InMemoryPreferenceStore:
    """语义/情节记忆内存替身：四操作 + 失效不删行（仅测试注入用）。

    Attributes:
        preferences: user_id → 用户偏好（内存中的语义记忆本体）。
        events: 按追加顺序排列的事件列表（供审计断言）。
    """

    def __init__(self) -> None:
        """初始化空偏好表与空事件列表。"""
        self.preferences: dict[str, UserPreference] = {}
        self.events: list[PreferenceEvent] = []

    def get(self, user_id: str) -> UserPreference:
        """读取偏好；无记录返回空偏好（version=1）。"""
        pref = self.preferences.get(user_id)
        return pref if pref else UserPreference(user_id=user_id)

    def apply(
        self,
        user_id: str,
        session_id: str | None,
        field_name: PreferenceField,
        new_value: object,
        source: PreferenceSource,
        trace_id: str,
    ) -> tuple[UserPreference, PreferenceEvent]:
        """四操作写入：裁定后更新偏好并追加事件。"""
        value = cast(JsonValue, new_value)
        pref = self.preferences.setdefault(user_id, UserPreference(user_id=user_id))
        existing = pref.fields.get(field_name)
        decision = decide_operation(existing, source)
        now = utc_now_iso()
        event = PreferenceEvent(
            user_id=user_id,
            session_id=session_id,
            field_name=field_name,
            old_value=existing.value if existing else None,
            new_value=value,
            source=source,
            source_weight=SOURCE_WEIGHT[source],
            decision=decision,
            trace_id=trace_id,
            created_at=now,
        )
        if decision in (PreferenceDecision.ADD, PreferenceDecision.UPDATE):
            pref.fields[field_name] = FieldEvidence(
                value=value,
                source=source,
                weight=SOURCE_WEIGHT[source],
                evidence_count=existing.evidence_count + 1 if existing else 1,
                updated_at=now,
            )
            pref.version += 1
            if decision is PreferenceDecision.UPDATE:
                self._mark(user_id, field_name, "superseded_at", now)
        self.events.append(event)
        return pref, event

    def clear(
        self,
        user_id: str,
        fields: list[PreferenceField] | None,
        trace_id: str,
    ) -> UserPreference:
        """清除全字段或指定字段：置空、version+1、相关事件标 revoked_at。"""
        pref = self.preferences.setdefault(user_id, UserPreference(user_id=user_id))
        targets = list(pref.fields) if fields is None else fields
        now = utc_now_iso()
        for field_name in targets:
            old = pref.fields.pop(field_name, None)
            if old is None:
                continue
            self._mark(user_id, field_name, "revoked_at", now)
            self.events.append(
                PreferenceEvent(
                    user_id=user_id,
                    session_id=None,
                    field_name=field_name,
                    old_value=old.value,
                    new_value=None,
                    source=PreferenceSource.EXPLICIT,
                    source_weight=SOURCE_WEIGHT[PreferenceSource.EXPLICIT],
                    decision=PreferenceDecision.DELETE,
                    trace_id=trace_id,
                    created_at=now,
                    revoked_at=now,
                )
            )
        pref.version += 1
        return pref

    def _mark(
        self,
        user_id: str,
        field_name: PreferenceField,
        column: str,
        now: str,
    ) -> None:
        """把该用户该字段的 ADD/UPDATE 事件替换失效标记（不删行）。"""
        self.events = [
            replace(event, **{column: now})
            if (
                event.user_id == user_id
                and event.field_name is field_name
                and event.decision
                in (PreferenceDecision.ADD, PreferenceDecision.UPDATE)
                and getattr(event, column) is None
            )
            else event
            for event in self.events
        ]


class InMemoryProcedureStore:
    """程序记忆内存替身：采纳 +1 / 撤销 -1，归零失效不删行。

    Attributes:
        procedures: (user_id, recipe_id) → 采纳记录。
    """

    def __init__(self) -> None:
        """初始化空采纳表。"""
        self.procedures: dict[tuple[str, str], UserProcedure] = {}

    def get_for_type(self, user_id: str, widget_type: str) -> list[UserProcedure]:
        """按用户 + 控件类型读采纳记录（含归零行）。"""
        return [
            p
            for p in self.procedures.values()
            if p.user_id == user_id and p.widget_type == widget_type
        ]

    def record_adopt(
        self,
        user_id: str,
        recipe_id: str,
        widget_type: str,
        trace_id: str,
    ) -> UserProcedure:
        """显式采纳 +1（首次插入为 1），version+1。"""
        key = (user_id, recipe_id)
        old = self.procedures.get(key)
        now = utc_now_iso()
        updated = UserProcedure(
            user_id=user_id,
            recipe_id=recipe_id,
            widget_type=widget_type,
            adopt_count=old.adopt_count + 1 if old else 1,
            last_adopted_at=now,
            version=old.version + 1 if old else 1,
        )
        self.procedures[key] = updated
        return updated

    def record_revoke(self, user_id: str, recipe_id: str) -> None:
        """采纳撤销 -1（下限 0，归零不删行），version+1。"""
        key = (user_id, recipe_id)
        old = self.procedures.get(key)
        if old is None:
            return
        self.procedures[key] = replace(
            old,
            adopt_count=max(old.adopt_count - 1, 0),
            version=old.version + 1,
        )
