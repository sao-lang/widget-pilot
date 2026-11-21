"""M06 REST 端点测试（§4.1）：guardrails API 的 hits 只读查询（skills 已删除，纯按需加载）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.guardrails.api.main import create_app
from services.guardrails.types import GuardHitType, InputGuardHit
from tests.support import InMemoryGuardHitStore, log_hit


def _store_with_hits(n: int = 2) -> InMemoryGuardHitStore:
    store = InMemoryGuardHitStore()
    for i in range(n):
        log_hit(
            "s",
            f"r{i}",
            InputGuardHit(
                GuardHitType.INJECTION, "INJ_IGNORE_RULES", "判据", f"片段{i}"
            ),
            "t",
            store,
        )
    return store


def test_healthz_and_readyz_preserved() -> None:
    """M01 健康探针保留（接入端点不破坏）。"""
    client = TestClient(create_app(hit_store=InMemoryGuardHitStore()))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_guardrails_hits_endpoint() -> None:
    """§4.1：GET /api/guardrails/hits 返回 hits + FPR + next_cursor。"""
    client = TestClient(
        create_app(hit_store=_store_with_hits(2), labeled_should_pass=10)
    )
    resp = client.get("/api/guardrails/hits")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["items"]) == 2
    assert data["items"][0]["rule_id"] == "INJ_IGNORE_RULES"
    assert data["false_positive_rate"] == 0.0  # 2 命中均未标注 should_pass
    assert data["next_cursor"] is None


def test_guardrails_hits_filter_and_pagination() -> None:
    """§4.1：hit_type / rule_id 过滤 + cursor 分页。"""
    client = TestClient(create_app(hit_store=_store_with_hits(3)))
    resp = client.get("/api/guardrails/hits?hit_type=INJECTION&limit=2&cursor=0")
    data = resp.json()["data"]
    assert len(data["items"]) == 2
    assert data["next_cursor"] == 2
    resp2 = client.get("/api/guardrails/hits?rule_id=NOPE")
    assert resp2.json()["data"]["items"] == []


def test_skills_endpoint_removed() -> None:
    """ADR-003：技能预取已删除，/api/skills 不再存在（404），不留兼容端点。"""
    client = TestClient(create_app(hit_store=InMemoryGuardHitStore()))
    assert client.get("/api/skills").status_code == 404


def test_endpoint_returns_unified_api_response_envelope() -> None:
    """M4（v1.8）：hits 端点响应含 ApiResponse 统一信封五字段。"""
    client = TestClient(create_app(hit_store=_store_with_hits(1)))
    body = client.get("/api/guardrails/hits").json()
    assert body["code"] == "OK"
    assert body["message"] == "OK"
    assert body["traceId"]  # 非空 traceId
    assert body["retryable"] is False
    assert "data" in body
    # m1（v1.8）：GuardHit.id 契约是 string，运行时 int 须转 str
    hits = body["data"]["items"]
    assert hits and all(isinstance(h["id"], str) for h in hits)


def test_illegal_enum_filter_returns_422() -> None:
    """N3：非法枚举过滤参数返回 422，不静默空列表。"""
    client = TestClient(create_app(hit_store=InMemoryGuardHitStore()))
    assert client.get("/api/guardrails/hits?hit_type=bogus").status_code == 422
