"""M04 审计 hash 链单测（§8.2 A18：可完整重放一次会话）。"""

from __future__ import annotations

import pytest

from services.control_plane.audit.ledger import AuditActions, AuditLedger


@pytest.fixture
def ledger() -> AuditLedger:
    return AuditLedger()


class TestAuditChain:
    """只追加 + hash 链防篡改。"""

    def test_append_builds_chain(self, ledger: AuditLedger) -> None:
        """连续追加：prev_hash 逐条链接，chain 可完整校验（A18）。"""
        r1 = ledger.append(
            "u1", "s1", AuditActions.STATE_TRANSITION, "OK", {"from": "DRAFT"}, "t1"
        )
        r2 = ledger.append("u1", "s1", AuditActions.INTERRUPT, "OK", {"rev": 1}, "t2")
        assert r1.prev_hash == ""
        assert r2.prev_hash == r1.chain_hash
        assert ledger.chain_valid()

    def test_tamper_detected(self, ledger: AuditLedger) -> None:
        """篡改任一环 payload 后 chain_valid 返回 False（§7 防篡改）。"""
        ledger.append("u1", "s1", AuditActions.RESUME, "OK", {"rev": 1}, "t1")
        # 直接改内部行模拟篡改
        ledger._rows[0].payload["rev"] = 999  # type: ignore[attr-defined]
        assert not ledger.chain_valid()

    def test_by_session_filters(self, ledger: AuditLedger) -> None:
        """按会话过滤审计事件（M11 重放入口）。"""
        ledger.append("u1", "s1", AuditActions.CANCEL, "OK", {}, "t1")
        ledger.append("u1", "s2", AuditActions.CANCEL, "OK", {}, "t2")
        assert len(ledger.by_session("s1")) == 1

    def test_deny_audit_records_no_pii(self, ledger: AuditLedger) -> None:
        """越权拒绝写审计，但 payload 不含资源内容（§7 合规）。"""
        row = ledger.append(
            "u2", "s1", AuditActions.DENY, "FORBIDDEN", {"actor": "u2"}, "t3"
        )
        assert row.action == AuditActions.DENY
        assert "session" not in str(row.payload).lower()
