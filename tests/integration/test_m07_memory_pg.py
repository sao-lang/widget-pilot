"""M07 记忆三表真 PG 集成测试（ADR-002 D-9~D-14）。

验证四操作真落库与读回、低权重不覆盖高权重（I-M3）、hash 链篡改检测、
租户隔离（I-M7）、清除后版本与事件保留、程序记忆采纳/撤销计数——
全部走真实 PG，不做内存替身。PG 不可达则 skip（不冒充通过）。

前置：docker compose --profile core up -d + alembic upgrade head。
"""
from __future__ import annotations

import pytest

from services.orchestrator.memory.pg_stores import (
    PgPreferenceStore,
    PgProcedureStore,
)
from services.orchestrator.memory.types import (
    PreferenceDecision,
    PreferenceField,
    PreferenceSource,
)
from tests.support.pg import pg_available, pg_connection, pg_execute, pg_fetchall

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
_TABLES = ("user_preference", "preference_event", "user_procedure")


def _infra_available() -> bool:
    """探测 PG 可达性；不可达则 skip。"""
    return pg_available(PG_DSN)


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_memory_tables() -> None:
    """每例前 TRUNCATE 三张记忆表并重置自增 id（集成测试隔离）。"""
    with pg_connection(PG_DSN) as conn:
        pg_execute(conn, f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY")


def _event_rows() -> list[dict[str, object]]:
    """从 PG 读回全部事件行（按 id 升序，dict 形式便于断言）。"""
    with pg_connection(PG_DSN) as conn:
        rows = pg_fetchall(
            conn,
            "SELECT id, user_id, field_name, old_value, new_value, source, "
            "decision, superseded_at, revoked_at, trace_id, row_hash "
            "FROM preference_event ORDER BY id",
        )
    return [dict(row._mapping) for row in rows]


class TestPgPreferenceStore:
    """语义 + 情节记忆真 PG 读写。"""

    def test_four_operations_persist_and_read_back(self) -> None:
        """ADD/UPDATE/NOOP/DELETE 真落库；新实例读回一致，事件四决策齐全。"""
        store = PgPreferenceStore(PG_DSN)
        _, added = store.apply(
            "u1", "s1", PreferenceField.PALETTE, "4x4", PreferenceSource.EXPLICIT, "t1"
        )
        assert added.decision is PreferenceDecision.ADD
        assert added.row_hash and len(added.row_hash) == 64
        pref, updated = store.apply(
            "u1", None, PreferenceField.PALETTE, "2x2", PreferenceSource.EXPLICIT, "t2"
        )
        assert updated.decision is PreferenceDecision.UPDATE
        assert pref.version == 3
        pref, nooped = store.apply(
            "u1", None, PreferenceField.PALETTE, "1x1", PreferenceSource.ADOPTED_ARTIFACT, "t3"
        )
        assert nooped.decision is PreferenceDecision.NOOP
        assert pref.version == 3
        cleared = store.clear("u1", [PreferenceField.PALETTE], "t4")
        assert cleared.version == 4
        assert cleared.fields == {}

        fresh = PgPreferenceStore(PG_DSN)  # 新实例从库读，证明非内存
        assert fresh.get("u1").fields == {}
        assert fresh.get("u1").version == 4
        decisions = [row["decision"] for row in _event_rows()]
        assert decisions == ["ADD", "UPDATE", "NOOP", "DELETE"]
        assert fresh.verify_event_chain() is True

    def test_low_weight_never_overwrites_high_weight(self) -> None:
        """I-M3：采纳信号（0.3）不能覆盖显式信号（1.0）→ NOOP 且版本不变。"""
        store = PgPreferenceStore(PG_DSN)
        store.apply(
            "u1", None, PreferenceField.PALETTE, "4x4", PreferenceSource.EXPLICIT, "t1"
        )
        pref, event = store.apply(
            "u1", None, PreferenceField.PALETTE, "1x1", PreferenceSource.ADOPTED_ARTIFACT, "t2"
        )
        assert event.decision is PreferenceDecision.NOOP
        assert pref.version == 2
        assert pref.fields[PreferenceField.PALETTE].value == "4x4"
        assert PgPreferenceStore(PG_DSN).get("u1").fields[
            PreferenceField.PALETTE
        ].value == "4x4"

    def test_hash_chain_tamper_detected(self) -> None:
        """篡改已落库事件的 new_value 后验链返回 False（防篡改生效）。"""
        store = PgPreferenceStore(PG_DSN)
        store.apply(
            "u1", None, PreferenceField.PALETTE, "4x4", PreferenceSource.EXPLICIT, "t1"
        )
        store.apply(
            "u1", None, PreferenceField.PALETTE, "2x2", PreferenceSource.EXPLICIT, "t2"
        )
        assert store.verify_event_chain() is True
        with pg_connection(PG_DSN) as conn:
            pg_execute(
                conn,
                "UPDATE preference_event SET new_value = '\"TAMPERED\"'::jsonb "
                "WHERE id = 1",
            )
        assert store.verify_event_chain() is False

    def test_tenant_isolation(self) -> None:
        """I-M7：跨用户 get 不可读，清除一用户不影响另一用户。"""
        store = PgPreferenceStore(PG_DSN)
        store.apply(
            "u1", None, PreferenceField.PALETTE, "4x4", PreferenceSource.EXPLICIT, "t1"
        )
        assert store.get("u2").fields == {}
        store.clear("u2", None, "t2")
        store.apply(
            "u2", None, PreferenceField.PALETTE, "1x1", PreferenceSource.EXPLICIT, "t3"
        )
        assert store.get("u1").fields[PreferenceField.PALETTE].value == "4x4"
        assert store.get("u2").fields[PreferenceField.PALETTE].value == "1x1"
        assert all(row["user_id"] in ("u1", "u2") for row in _event_rows())

    def test_clear_increments_version_and_keeps_revoked_events(self) -> None:
        """I-M4：clear 后 version+1、读取为空；事件不删行且保留 revoked_at。"""
        store = PgPreferenceStore(PG_DSN)
        store.apply(
            "u1", None, PreferenceField.PALETTE, "4x4", PreferenceSource.EXPLICIT, "t1"
        )
        store.apply(
            "u1", None, PreferenceField.PREFERRED_GRIDS, ["4x4"], PreferenceSource.EXPLICIT, "t2"
        )
        cleared = store.clear("u1", None, "t3")
        assert cleared.version == 4
        assert cleared.fields == {}
        assert PgPreferenceStore(PG_DSN).get("u1").version == 4
        rows = _event_rows()
        assert len(rows) == 4  # 2 ADD + 2 DELETE，一条未删
        assert [row["decision"] for row in rows].count("DELETE") == 2
        assert all(row["revoked_at"] is not None for row in rows)


class TestPgProcedureStore:
    """程序记忆真 PG 采纳/撤销计数。"""

    def test_adopt_and_revoke_counts(self) -> None:
        """采纳 +1、撤销 -1、归零不删行、version 每次 +1。"""
        store = PgProcedureStore(PG_DSN)
        first = store.record_adopt("u1", "r1", "chart", "t1")
        assert first.adopt_count == 1 and first.version == 1
        second = store.record_adopt("u1", "r1", "chart", "t2")
        assert second.adopt_count == 2 and second.version == 2
        assert len(store.get_for_type("u1", "chart")) == 1
        assert len(PgProcedureStore(PG_DSN).get_for_type("u1", "chart")) == 1
        store.record_revoke("u1", "r1")
        after_one = store.get_for_type("u1", "chart")[0]
        assert after_one.adopt_count == 1 and after_one.version == 3
        store.record_revoke("u1", "r1")
        zeroed = store.get_for_type("u1", "chart")[0]
        assert zeroed.adopt_count == 0 and zeroed.version == 4
        store.record_revoke("u1", "r1")
        assert store.get_for_type("u1", "chart")[0].adopt_count == 0
        assert store.get_for_type("u2", "chart") == []
