"""M06 §2.3 / runbook §2：guard_hit PG 留证失败 → 保守拒绝放行。

用 monkeypatch 让 validate_chain 抛 SQLAlchemyError（模拟 PG 不可用/写留证失败），
验证装配层把它转成拒绝结论（ok=False），绝不降级内存放行。
"""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError

import services.orchestrator.guardrail_adapter as adapter
from packages.contracts.enums import ReasonCode
from packages.contracts.types import TaskFields


def test_pg_unavailable_returns_reject_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """PG 抛错时 build_validator 返回拒绝结论，不放行进模型。"""
    def boom(**kwargs: object) -> None:
        """模拟 guard_hit 留证写 PG 失败。"""
        raise SQLAlchemyError("simulated PG outage")

    class _UnusedRedisClient:
        """占位 Redis 客户端：boom 先抛错，客户端方法不会被调用。"""

    monkeypatch.setattr(adapter, "validate_chain", boom)
    monkeypatch.setattr(adapter.redis, "from_url", lambda url: _UnusedRedisClient())

    validator = adapter.build_validator(lambda att_id: None, "redis://unused", dsn="postgresql://x")
    result = validator(
        "做一个日历",
        cast(
            TaskFields,
            {"session_id": "s1", "run_id": "r1", "description": "做一个日历"},
        ),
    )

    assert result.ok is False
    assert result.reason_code is ReasonCode.GUARD_INPUT_HIT
    # m2：evidence_ref 按 §4.4 带 sha256 快照 hash（可回放），非裸字符串
    assert result.evidence_ref and "#sha256:" in result.evidence_ref
