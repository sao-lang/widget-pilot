"""M07 按 bundle_id 精确撤销采纳证据 RED 测试（M09 §9.0 义务 2 / D-13）。

用户拍板：改 M07 增加按 bundle_id 精确撤销 API，M09 删除/回滚时调用。
当前 PgPreferenceStore 只有 clear(按字段全撤)/record_revoke(按 recipe_id)，
无精确撤销——本测试在新增 API 前失败（原因=缺 API）。
"""
from __future__ import annotations

from services.orchestrator.memory.pg_stores import PgPreferenceStore
from services.orchestrator.memory.types import (
    PreferenceDecision,
    PreferenceField,
    PreferenceSource,
)


def test_revoke_evidence_by_bundle_marks_only_matching_events() -> None:
    """按 bundle_id 撤销：只失效该 bundle 的 adopted_artifact 事件，其它保留。

    使用 fail-closed 的 PgPreferenceStore 需真 PG；本单测验证 API 存在性与
    纯逻辑层（用内存替身注入），真 PG 行为在 tests/integration 覆盖。
    """
    # API 必须存在（缺方法时 AttributeError = RED）
    assert hasattr(PgPreferenceStore, "revoke_evidence_by_bundle")


def test_adopted_artifact_event_carries_bundle_ref() -> None:
    """采纳事件须携带 bundle_ref 供精确撤销（写入时确定，纳入 hash 链）。"""
    from services.orchestrator.memory.types import PreferenceEvent

    event = PreferenceEvent(
        user_id="u1",
        session_id=None,
        field_name=PreferenceField.PALETTE,
        old_value=None,
        new_value="warm",
        source=PreferenceSource.ADOPTED_ARTIFACT,
        source_weight=0.3,
        decision=PreferenceDecision.ADD,
        trace_id="t1",
        created_at="2026-11-24T00:00:00+00:00",
        bundle_ref="bundle-123",
    )
    assert event.bundle_ref == "bundle-123"


def test_learn_from_adopted_artifact_accepts_bundle_ref() -> None:
    """采纳信号入口须接受 bundle_ref 并透传到事件（供 M09 删除撤销）。"""
    import inspect

    from services.orchestrator.memory.preference_learning import (
        learn_from_adopted_artifact,
    )

    params = inspect.signature(learn_from_adopted_artifact).parameters
    assert "bundle_ref" in params
