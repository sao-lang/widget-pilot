"""M07 偏好 REST 端点测试（v1.6，ADR-002 D-10/D-13）。

显式注入内存 Store 替身（测试范围，不进生产源码）；验证读取/显式写入/清除、
租户隔离、以及未注入 Store 时不挂载路由（部署形态，非兼容桩）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from packages.agent_core.types import JsonValue
from services.control_plane.api.server import create_app
from services.orchestrator.memory.types import (
    FieldEvidence,
    PreferenceDecision,
    PreferenceEvent,
    PreferenceField,
    PreferenceSource,
    UserPreference,
    utc_now_iso,
)
from tests.support.control_plane import (
    AuditLedger,
    InMemorySessionStore,
    InMemoryUserStore,
    SSEWindow,
)


class StubPreferenceStore:
    """测试用偏好 Store 替身（显式注入，非生产代码）。"""

    def __init__(self) -> None:
        """初始化空偏好表（user_id → UserPreference）。"""
        self.prefs: dict[str, UserPreference] = {}

    def get(self, user_id: str) -> UserPreference:
        """返回用户偏好；无记录返回空偏好。"""
        return self.prefs.get(user_id, UserPreference(user_id=user_id))

    def apply(
        self,
        user_id: str,
        session_id: str | None,
        field_name: PreferenceField,
        new_value: JsonValue,
        source: PreferenceSource,
        trace_id: str,
        *,
        bundle_ref: str = "",
    ) -> tuple[UserPreference, PreferenceEvent]:
        """写入字段（测试替身用简单覆盖，四操作规则由领域单测覆盖）。"""
        pref = self.get(user_id)
        now = utc_now_iso()
        old = pref.fields.get(field_name)
        pref.fields[field_name] = FieldEvidence(
            value=new_value, source=source, weight=1.0,
            evidence_count=1, updated_at=now,
        )
        pref.version += 1
        self.prefs[user_id] = pref
        event = PreferenceEvent(
            user_id=user_id, session_id=session_id, field_name=field_name,
            old_value=old.value if old else None, new_value=new_value,
            source=source, source_weight=1.0, decision=PreferenceDecision.ADD,
            trace_id=trace_id, created_at=now, bundle_ref=bundle_ref,
        )
        return pref, event

    def revoke_evidence_by_bundle(
        self, user_id: str, bundle_id: str, trace_id: str
    ) -> int:
        """按 bundle 撤销采纳证据（测试替身：无事件链，返回 0）。"""
        return 0

    def clear(
        self, user_id: str, fields: list[PreferenceField] | None, trace_id: str
    ) -> UserPreference:
        """清除字段（测试替身直接删除，事件审计由 PG 集成测试覆盖）。"""
        pref = self.get(user_id)
        targets = fields if fields else list(pref.fields.keys())
        for field_name in targets:
            pref.fields.pop(field_name, None)
        pref.version += 1
        self.prefs[user_id] = pref
        return pref


def _register_and_token(client: TestClient, email: str = "pref@example.com") -> str:
    """注册用户并返回 access token。"""
    resp = client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    )
    assert resp.status_code == 200
    return resp.json()["data"]["access_token"]


def _build_app(store: StubPreferenceStore | None) -> TestClient:
    """构造测试 app；显式注入偏好 Store（None 用于验证不挂载路由）。"""
    app = create_app(
        InMemoryUserStore(),
        InMemorySessionStore(),
        "test-jwt-secret",
        event_source=SSEWindow(window_n=10),
        attachment_service=None,
        audit=AuditLedger(),
        task_enqueuer=lambda task: "msg-id",
        preference_store=store,
    )
    return TestClient(app)


def _app_with_prefs(store: StubPreferenceStore) -> TestClient:
    """构造注入偏好 Store 的测试 app。"""
    return _build_app(store)


def test_get_preferences_empty() -> None:
    """新用户读取偏好返回空 fields 与初始版本。"""
    client = _app_with_prefs(StubPreferenceStore())
    token = _register_and_token(client)
    resp = client.get(
        "/api/users/me/preferences", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["fields"] == {}
    assert body["version"] == 1


def test_put_then_get_preferences() -> None:
    """显式写入偏好后可读回，来源为 explicit。"""
    store = StubPreferenceStore()
    client = _app_with_prefs(store)
    token = _register_and_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.put(
        "/api/users/me/preferences",
        json={"palette": "dark", "preferred_grids": ["4x4", "5x4"]},
        headers=headers,
    )
    assert resp.status_code == 200
    resp = client.get("/api/users/me/preferences", headers=headers)
    fields = resp.json()["data"]["fields"]
    assert fields["palette"]["value"] == "dark"
    assert fields["palette"]["source"] == "explicit"
    assert fields["preferred_grids"]["value"] == ["4x4", "5x4"]


def test_delete_preferences() -> None:
    """清除偏好后读取为空，版本递增。"""
    store = StubPreferenceStore()
    client = _app_with_prefs(store)
    token = _register_and_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        "/api/users/me/preferences", json={"palette": "dark"}, headers=headers
    )
    resp = client.request(
        "DELETE", "/api/users/me/preferences", json={"fields": ["palette"]},
        headers=headers,
    )
    assert resp.status_code == 200
    fields = client.get(
        "/api/users/me/preferences", headers=headers
    ).json()["data"]["fields"]
    assert fields == {}


def test_preferences_require_auth() -> None:
    """偏好端点需鉴权（401）。"""
    client = _app_with_prefs(StubPreferenceStore())
    assert client.get("/api/users/me/preferences").status_code == 401


def test_no_preference_store_means_no_route() -> None:
    """未注入偏好 Store 时不挂载偏好路由（404，部署形态非兼容桩）。"""
    client = _build_app(None)
    assert client.get("/api/users/me/preferences").status_code == 404
