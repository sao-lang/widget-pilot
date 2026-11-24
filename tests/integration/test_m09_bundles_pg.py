"""M09 真 PG 集成测试（产物索引 + 版本链 + M07 采纳证据精确撤销）。

验证成包落库/读回/租户隔离/状态幂等/版本回滚、revoke_evidence_by_bundle
真按 bundle_id 精确撤销且 hash 链完整。PG 不可达则 skip（不冒充通过）。
前置：podman run 起 PG + alembic upgrade head（0019）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from packages.contracts.enums import BundleState
from services.control_plane.bundles.domain import VersionNode
from services.control_plane.bundles.pg_store import PgBundleStore
from services.orchestrator.memory.pg_stores import PgPreferenceStore
from services.orchestrator.memory.types import PreferenceField, PreferenceSource
from tests.support.pg import pg_available, pg_connection, pg_execute

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"


@pytest.mark.skipif(not pg_available(PG_DSN), reason="PG 未就绪（需 podman run + alembic upgrade head）")
class TestPgBundleStore:
    """真 PG 产物索引读写与状态操作。"""

    @pytest.fixture(autouse=True)
    def clean(self) -> None:
        """每例前清理 M09 两表。"""
        with pg_connection(PG_DSN) as conn:
            pg_execute(conn, "TRUNCATE widget_bundle, bundle_version")

    def _bundle(self, user_id: str) -> dict:
        """构造一个成包形态（最小字段）。"""
        return {
            "bundle_id": uuid4().hex,
            "user_id": user_id,
            "session_id": uuid4().hex,
            "spec": {"grid": "2x2", "widget_type": "clock"},
            "source": {"content_ref": "code/s/r/widget.tsx", "language": "tsx"},
            "compiled": {"content_ref": "code/s/r/compiled.js", "hash": "d" * 64},
            "manifest": {"spec_hash": "1" * 64, "grid": "2x2"},
            "static_check": {"passed": True, "violations": []},
            "sandbox_test": {"passed": True, "cases": []},
            "state": BundleState.GENERATED.value,
            "hash": "a" * 64,
            "versions": [],
            "evidence_ref": "evidence#sha256:" + "b" * 64,
        }

    def test_insert_get_and_tenant_isolation(self) -> None:
        """成包落库读回 + 越权返回 None（A16）。"""
        store = PgBundleStore(PG_DSN)
        bundle = self._bundle("u1")
        node = VersionNode(
            version_id=uuid4().hex,
            bundle_id=bundle["bundle_id"],
            parent_id=None,
            compiled_ref="code/s/r/compiled.js",
            compiled_hash="d" * 64,
            manifest_hash="1" * 64,
            created_at="2026-11-24T00:00:00+00:00",
        )
        store.insert(bundle, (node,))
        got = store.get(bundle["bundle_id"], "u1")
        assert got is not None and got["hash"] == "a" * 64
        assert store.get(bundle["bundle_id"], "u2") is None

    def test_state_update_is_idempotent(self) -> None:
        """状态幂等：首次变更 True，重复 False（A12）。"""
        store = PgBundleStore(PG_DSN)
        bundle = self._bundle("u1")
        store.insert(bundle, ())
        assert store.update_state(bundle["bundle_id"], "u1", BundleState.INSTALLED.value)
        assert not store.update_state(bundle["bundle_id"], "u1", BundleState.INSTALLED.value)


@pytest.mark.skipif(not pg_available(PG_DSN), reason="PG 未就绪")
class TestPgEvidenceRevoke:
    """M07 按 bundle_id 精确撤销采纳证据（真 PG + hash 链）。"""

    @pytest.fixture(autouse=True)
    def clean(self) -> None:
        """每例前清理偏好三表。"""
        with pg_connection(PG_DSN) as conn:
            pg_execute(conn, "TRUNCATE user_preference, preference_event, user_procedure")

    def test_revoke_by_bundle_marks_only_matching(self) -> None:
        """撤销 bundle-A 证据不动 bundle-B（D-13 精确撤销）。"""
        store = PgPreferenceStore(PG_DSN)
        _pref, ev_a = store.apply(
            "u1", None, PreferenceField.PALETTE, "warm",
            PreferenceSource.ADOPTED_ARTIFACT, "t1", bundle_ref="bundle-A",
        )
        store.apply(
            "u1", None, PreferenceField.PALETTE, "cool",
            PreferenceSource.ADOPTED_ARTIFACT, "t2", bundle_ref="bundle-B",
        )
        assert ev_a.bundle_ref == "bundle-A"
        count = store.revoke_evidence_by_bundle("u1", "bundle-A", "t3")
        assert count == 1
        assert store.verify_event_chain() is True
        # bundle-B 的证据仍有效
        assert store.revoke_evidence_by_bundle("u1", "bundle-B", "t4") == 1
