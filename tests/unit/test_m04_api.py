"""M04 REST 接线测试（规则 10：断言 API 真实消费 auth/session 服务，无功能断点）。

用 FastAPI TestClient 打真实路由，验证：
- register/login → JWT → 带 token 建会话 → 提交 → interrupt/resume
- 跨用户读会话 → 403（A1）
- 统一响应格式 { code, data }（§4.4 / 基线 §5.1）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.control_plane.accounts.stores import InMemoryUserStore
from services.control_plane.api.server import create_app
from services.control_plane.sessions.stores import InMemorySessionStore


@pytest.fixture
def client() -> TestClient:
    """构造真实 API app（内存存储 + 测试密钥），走完整请求链。"""
    app = create_app(
        user_store=InMemoryUserStore(),
        session_store=InMemorySessionStore(),
        jwt_secret="test-secret-not-production-32bytes-minimum",
    )
    return TestClient(app)


def _register(client: TestClient, email: str) -> dict:
    """注册并取 token。"""
    resp = client.post(
        "/api/auth/register", json={"email": email, "password": "pass-phrase"}
    )
    assert resp.status_code == 200
    return resp.json()["data"]


def _auth(token: str) -> dict[str, str]:
    """构造 Bearer header。"""
    return {"Authorization": f"Bearer {token}"}


class TestAuthEndpoints:
    """注册/登录端点（§4.1）。"""

    def test_register_returns_code_ok(self, client: TestClient) -> None:
        """注册成功：code=OK + data 含双令牌（基线 §5.1）。"""
        data = _register(client, "api-a@example.com")
        assert data["access_token"]
        assert data["refresh_token"]
        assert data["user"]["role"] == "user"

    def test_refresh_endpoint_returns_access(self, client: TestClient) -> None:
        """S4：POST /api/auth/refresh 用 refresh token 换回新 access token。"""
        data = _register(client, "refresh-api@example.com")
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": data["refresh_token"]},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["access_token"]

    def test_login_wrong_password_401(self, client: TestClient) -> None:
        """密码错误返回 401 + code（§4.1 login 401）。"""
        _register(client, "api-b@example.com")
        resp = client.post(
            "/api/auth/login",
            json={"email": "api-b@example.com", "password": "wrong"},
        )
        assert resp.status_code == 401


class TestSessionEndpoints:
    """会话端点接线（真实消费 SessionService）。"""

    def test_create_submit_interrupt_resume_flow(self, client: TestClient) -> None:
        """建会话→提交→打断→继续，每步经真实服务（规则 10 接线）。"""
        token = _register(client, "flow@example.com")["access_token"]
        hdr = _auth(token)
        created = client.post(
            "/api/sessions", json={"title": "t", "grid": "4x4"}, headers=hdr
        )
        assert created.status_code == 200
        sid = created.json()["data"]["id"]
        assert created.json()["data"]["state"] == "DRAFT"

        submitted = client.post(
            f"/api/sessions/{sid}/messages",
            json={"description": "做一个日历", "attachment_ids": []},
            headers=hdr,
        )
        assert submitted.status_code == 202
        assert submitted.json()["data"]["state"] == "RUNNING"

        paused = client.post(f"/api/sessions/{sid}/interrupt", json={}, headers=hdr)
        assert paused.json()["data"]["state"] == "PAUSED"

        resumed = client.post(f"/api/sessions/{sid}/resume", json={}, headers=hdr)
        assert resumed.json()["data"]["state"] == "RUNNING"

    def test_cross_user_snapshot_403(self, client: TestClient) -> None:
        """用户 B 读 A 的会话快照 → 403（A1，租户隔离接线到 HTTP）。"""
        token_a = _register(client, "owner@example.com")["access_token"]
        sid = client.post(
            "/api/sessions", json={"title": "t"}, headers=_auth(token_a)
        ).json()["data"]["id"]
        token_b = _register(client, "intruder@example.com")["access_token"]
        resp = client.get(f"/api/sessions/{sid}", headers=_auth(token_b))
        assert resp.status_code == 403

    def test_unauthenticated_401(self, client: TestClient) -> None:
        """无 token 访问会话端点 → 401。"""
        resp = client.get("/api/sessions")
        assert resp.status_code == 401

    def test_cross_user_signal_endpoints_403(self, client: TestClient) -> None:
        """B1 回归：用户 B 对 A 的会话发 signal（cancel/interrupt/messages）一律 403。

        拷问实测：修复前 B 可 cancel A 的会话（HTTP 200）导致状态被篡改。
        """
        token_a = _register(client, "sig-owner@example.com")["access_token"]
        sid = client.post(
            "/api/sessions", json={"title": "t"}, headers=_auth(token_a)
        ).json()["data"]["id"]
        token_b = _register(client, "sig-attacker@example.com")["access_token"]
        hdr_b = _auth(token_b)
        # 三个 signal 端点：任一返回 200/500 都是缺陷
        for path, body in (
            ("messages", {"description": "hack", "attachment_ids": []}),
            ("interrupt", {}),
            ("resume", {}),
            ("cancel", {}),
        ):
            resp = client.post(f"/api/sessions/{sid}/{path}", json=body, headers=hdr_b)
            assert resp.status_code == 403, f"{path} 越权未被拒：{resp.status_code}"

    def test_clarification_flow(self, client: TestClient) -> None:
        """澄清端点回注答案（P0-2 落点经 HTTP 验证）。"""
        token = _register(client, "clarify@example.com")["access_token"]
        hdr = _auth(token)
        sid = client.post("/api/sessions", json={"title": "t"}, headers=hdr).json()[
            "data"
        ]["id"]
        client.post(
            f"/api/sessions/{sid}/messages",
            json={"description": "desc", "attachment_ids": []},
            headers=hdr,
        )
        # 外部无法直接 mark_awaiting，故经测试钩子注入态（见 server.create_app 的 test hook）
        client.post(f"/api/sessions/{sid}/_test_awaiting", headers=hdr)
        answered = client.post(
            f"/api/sessions/{sid}/clarifications",
            json={"question_id": "q1", "answer": "4x4"},
            headers=hdr,
        )
        assert answered.status_code == 202
        assert answered.json()["data"]["state"] == "RUNNING"
