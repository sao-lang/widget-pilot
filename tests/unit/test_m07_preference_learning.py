"""M07 偏好信号生产写入入口测试（v1.6，D-10 第 2/4 类 + D-14）。

显式注入 Store 替身，验证 clarification / adopted_artifact 两类信号真的走
四操作写入与程序记忆采纳；零 LLM 调用（I-M2）。
"""
from __future__ import annotations

from services.orchestrator.memory.preference_learning import (
    learn_from_adopted_artifact,
    learn_from_clarification,
)
from services.orchestrator.memory.types import (
    PreferenceField,
    PreferenceSource,
    UserPreference,
)
from tests.support.m07_stores import InMemoryPreferenceStore, InMemoryProcedureStore


def test_clarification_writes_preferred_grids() -> None:
    """澄清点选 grid → preferred_grids 写入，来源 clarification（0.8）。"""
    store = InMemoryPreferenceStore()
    pref = learn_from_clarification(store, "u1", "s1", "4x4", "tr-1")
    assert pref is not None
    evidence = pref.fields[PreferenceField.PREFERRED_GRIDS]
    assert evidence.value == ["4x4"]
    assert evidence.source is PreferenceSource.CLARIFICATION
    assert evidence.weight == 0.8


def test_clarification_no_signal_is_noop() -> None:
    """无 grid 信号 → 不写库（返回 None）。"""
    store = InMemoryPreferenceStore()
    assert learn_from_clarification(store, "u1", "s1", None, "tr-1") is None
    assert store.get("u1").fields == {}


def test_adopted_artifact_writes_palette_and_procedure() -> None:
    """采纳产物 → palette 偏好 + 程序记忆采纳 +1（零 LLM）。"""
    pref_store = InMemoryPreferenceStore()
    proc_store = InMemoryProcedureStore()
    pref, proc = learn_from_adopted_artifact(
        pref_store,
        proc_store,
        "u1",
        "dark",
        "recipe.calendar.month.v3",
        "calendar",
        "tr-2",
    )
    assert pref is not None
    assert pref.fields[PreferenceField.PALETTE].source is PreferenceSource.ADOPTED_ARTIFACT
    assert proc is not None
    assert proc.adopt_count == 1
    # 再次采纳 → 计数 +1
    _, proc2 = learn_from_adopted_artifact(
        pref_store, proc_store, "u1", None, "recipe.calendar.month.v3", "calendar", "tr-3"
    )
    assert proc2 is not None and proc2.adopt_count == 2


def test_adopted_artifact_without_palette_or_procedure() -> None:
    """无 palette / 无 recipe → 对应侧不写（None）。"""
    pref_store = InMemoryPreferenceStore()
    pref, proc = learn_from_adopted_artifact(
        pref_store, None, "u1", None, "recipe.x", "custom", "tr-4"
    )
    assert pref is None and proc is None
    assert isinstance(pref_store.get("u1"), UserPreference)
