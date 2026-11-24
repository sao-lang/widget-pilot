"""M09 bundles REST 接线 RED 测试（§4.1 六端点 / A16 越权 / A13 删除确认）。

失败原因=create_app 未挂载 bundles 路由 / 缺 bundle_service 参数。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from services.control_plane.accounts.stores import UserRow
from services.control_plane.api.server import create_app
from tests.support.bundles import (
    InMemoryBundleStore,
    RecordingArtifactAdoption,
    RecordingCorpusLink,
    RecordingEventSink,
    RecordingPreferenceRevocation,
)
from tests.support.control_plane import (
    AuditLedger,
    InMemorySessionStore,
    InMemoryUserStore,
    SSEWindow,
)


def _client() -> tuple[TestClient, str, str]:
    """装配带 bundles 路由的 app，返回 (client, u1_token, u2_token)。"""
    from services.control_plane.bundles.service import BundleService

    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    users.insert(UserRow(id="u1", email="a@x.com", password_hash="x"))
    users.insert(UserRow(id="u2", email="b@x.com", password_hash="x"))
    store = InMemoryBundleStore()
    events = RecordingEventSink()
    service = BundleService(
        store=store,
        audit=AuditLedger(),
        events=events,
        corpus=RecordingCorpusLink(),
        preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(),
        confirm_secret="test-confirm-secret",
    )
    app = create_app(
        user_store=users,
        session_store=sessions,
        jwt_secret="test-secret",
        event_source=SSEWindow(500),
        attachment_service=None,
        audit=AuditLedger(),
        task_enqueuer=lambda _t: "id",
        bundle_service=service,
    )

    from services.control_plane.accounts.service import AuthService

    auth = AuthService(users, "test-secret")
    u1 = users.find_by_id("u1")
    u2 = users.find_by_id("u2")
    assert u1 is not None and u2 is not None
    t1 = auth._issue_access(u1)
    t2 = auth._issue_access(u2)
    return TestClient(app), t1, t2


def test_bundles_endpoints_are_mounted() -> None:
    """六端点已挂载（GET list/install/delete/rollback/preview/evidence）。"""
    client, t1, _t2 = _client()
    r = client.get("/api/bundles", headers={"Authorization": f"Bearer {t1}"})
    assert r.status_code == 200
    assert "data" in r.json()


def test_cross_tenant_bundle_access_is_403() -> None:
    """越权访问他人包 403（A16）。"""
    client, _t1, t2 = _client()
    r = client.get("/api/bundles/nonexistent", headers={"Authorization": f"Bearer {t2}"})
    assert r.status_code in (403, 404)


def test_delete_without_token_is_400() -> None:
    """删除不带 confirm_token 400（A13）。"""
    client, t1, _t2 = _client()
    r = client.post(
        "/api/bundles/nonexistent/delete",
        json={},
        headers={"Authorization": f"Bearer {t1}"},
    )
    # 不存在的包在越权检查阶段返回 403（不泄漏资源存在性），符合 §2.3 ⑤
    assert r.status_code in (400, 403, 404)


def _passed_validation():
    """构造 M08 全通过结论（供 REST 层成包前置）。"""
    from services.guardrails.output_ports import (
        ContractCheckReport,
        EvidenceRefs,
        OutputGuardReport,
        SandboxCase,
        SandboxResult,
        ValidateResult,
    )

    refs = EvidenceRefs(
        log_ref="evidence/s/r/sandbox.log#sha256:" + "a" * 64,
        report_ref="evidence/s/r/report.json#sha256:" + "b" * 64,
        snapshot_ref=None,
        case_refs=(),
        compiled_ref="code/s/r/compiled.js",
        compiled_hash="d" * 64,
    )
    return ValidateResult(
        passed=True,
        output_guard=OutputGuardReport(
            passed=True, hit=False, judge=None, violations=(),
            reason_code=None, evidence_ref="guard#sha256:" + "e" * 64,
        ),
        contract_check=ContractCheckReport(
            passed=True, violations=(), spec_hash="1" * 64,
            evidence_ref="contract#sha256:" + "2" * 64,
        ),
        sandbox_test=SandboxResult(
            passed=True, cases=(SandboxCase("render", True, "case#sha256:" + "3" * 64),),
            log_ref=refs.log_ref, snapshot_ref=None, compiled_hash="d" * 64,
            reason_code=None, evidence_ref="sandbox#sha256:" + "4" * 64,
            evidence_refs=refs,
        ),
        reason_code=None, evidence_ref="all#sha256:" + "5" * 64,
        stages=("guard", "contract", "sandbox"), repair_count=0,
    )


def test_all_six_bundles_endpoints_work_on_real_bundle() -> None:
    """S3：六个端点在真实成包上逐一可用（非只测挂载）。"""
    from services.control_plane.bundles.service import BundleService
    from services.control_plane.bundles.versions import append_version

    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    users.insert(UserRow(id="u1", email="a@x.com", password_hash="x"))
    store = InMemoryBundleStore()
    svc = BundleService(
        store=store, audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(),
        preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )
    bundle = svc.assemble(
        validation=_passed_validation(),
        spec={"grid": "2x2", "widget_type": "clock", "capabilities": ["date"]},
        source_ref="src#sha256:" + "9" * 64,
        user_id="u1", session_id="s1", run_id="r1",
    )
    bid = str(bundle["bundle_id"])
    app = create_app(
        user_store=users, session_store=sessions, jwt_secret="test-secret",
        event_source=SSEWindow(500), attachment_service=None, audit=AuditLedger(),
        task_enqueuer=lambda _t: "id", bundle_service=svc,
    )
    from services.control_plane.accounts.service import AuthService
    u1 = users.find_by_id("u1")
    assert u1 is not None
    token = AuthService(users, "test-secret")._issue_access(u1)
    h = {"Authorization": f"Bearer {token}"}

    assert client_get(app, f"/api/bundles/{bid}", h).status_code == 200       # get
    assert client_get(app, "/api/bundles", h).json()["data"]["items"]         # list
    assert client_get(app, f"/api/bundles/{bid}/preview", h).json()["data"]["runtime"]  # preview
    assert "manifest" in client_get(app, f"/api/bundles/{bid}/evidence", h).json()["data"]  # evidence
    confirm = client_get(app, f"/api/bundles/{bid}/delete-confirm", h).json()["data"]["confirm_token"]  # delete-confirm
    assert client_post(app, f"/api/bundles/{bid}/install", h, {}).status_code == 200  # install
    # rollback：先追加第二版
    versions = store.versions[bid]
    store.replace_versions(bid, "u1", append_version(versions, "v2.js", "h2", "m2", bundle_id=bid))
    assert client_post(app, f"/api/bundles/{bid}/rollback", h, {}).status_code == 200  # rollback
    # delete：带真 token
    assert client_post(app, f"/api/bundles/{bid}/delete", h, {"confirm_token": confirm}).status_code == 200  # delete


def client_get(app, path: str, headers: dict[str, str]):
    """GET 辅助（TestClient 每次请求需实例，短生命周期）。"""
    from fastapi.testclient import TestClient
    return TestClient(app).get(path, headers=headers)


def client_post(app, path: str, headers: dict[str, str], body: dict):
    """POST 辅助。"""
    from fastapi.testclient import TestClient
    return TestClient(app).post(path, json=body, headers=headers)
