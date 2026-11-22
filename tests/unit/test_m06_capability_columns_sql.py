"""ADR-003：capability_audit_log INSERT 字段对齐（无需真实 PG）。

用 monkeypatch 捕获 ORM Session.add 的对象，断言列名/取值三者一致；
真实落库仍由 Docker PG 的集成测试验证。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from services.control_plane.models import CapabilityAuditLog
from services.guardrails import pg_stores
from services.guardrails.telemetry import CapabilityAuditLogRow
from services.guardrails.types import HitDecision


class _Session:
    """捕获 add 调用的假 Session。"""

    def __init__(self, added: list[object]) -> None:
        self.added = added

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _capture(monkeypatch: pytest.MonkeyPatch, added: list[object]) -> None:
    """替换 session_scope 为捕获写操作的假上下文。"""

    @contextmanager
    def fake_scope(factory: object) -> Iterator[_Session]:
        yield _Session(added)

    monkeypatch.setattr(pg_stores, "session_scope", fake_scope)


def test_capability_audit_insert_columns_aligned(monkeypatch: pytest.MonkeyPatch) -> None:
    """capability_audit_log 写入含 capability_id/level 且取值对齐。"""
    added: list[object] = []
    _capture(monkeypatch, added)
    store = pg_stores.PgCapabilityAuditLogStore("postgresql://x")
    store.append(
        CapabilityAuditLogRow(
            "s", "r", "recipe.calendar.month.v3", 2, 1, 220, HitDecision.REJECT,
            "DENIED", "t1",
        )
    )
    assert len(added) == 1
    obj = added[0]
    assert isinstance(obj, CapabilityAuditLog)
    assert obj.capability_id == "recipe.calendar.month.v3"
    assert obj.level == 2
    assert obj.decision == HitDecision.REJECT.value
