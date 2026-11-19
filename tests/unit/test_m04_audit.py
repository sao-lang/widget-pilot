"""M04 审计 hash 链单测（§8.2 A18：可完整重放一次会话）。"""

from __future__ import annotations

import pytest

from services.control_plane.audit.ledger import AuditActions, AuditLedger
from services.control_plane.sessions.service import SessionService
from services.control_plane.sessions.stores import InMemorySessionStore


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

    def test_tamper_action_detected(self, ledger: AuditLedger) -> None:
        """S6 回归：篡改 action（非 payload 字段）也必须被 hash 链检出。"""
        ledger.append("u1", "s1", AuditActions.RESUME, "OK", {}, "t1")
        # frozen dataclass：用 object.__setattr__ 模拟存储层被篡改
        object.__setattr__(ledger._rows[0], "action", AuditActions.DENY)  # type: ignore[attr-defined]
        assert not ledger.chain_valid()

    def test_by_session_filters(self, ledger: AuditLedger) -> None:
        """按会话过滤审计事件（M11 重放入口）。"""
        ledger.append("u1", "s1", AuditActions.CANCEL, "OK", {}, "t1")
        ledger.append("u1", "s2", AuditActions.CANCEL, "OK", {}, "t2")
        assert len(ledger.by_session("s1")) == 1

    def test_session_service_writes_audit(self, ledger: AuditLedger) -> None:
        """S1 回归：SessionService 状态迁移实时写审计账本（R10 生产接线）。"""
        svc = SessionService(InMemorySessionStore(), audit=ledger)
        sid = svc.create("u1", "t", "4x4").id
        svc.submit_message(sid, "u1", "desc", [])
        svc.interrupt(sid, "u1")
        rows = ledger.by_session(sid)
        assert len(rows) == 2  # 提交 + 打断
        assert rows[0].action == AuditActions.STATE_TRANSITION
        assert rows[1].action == AuditActions.INTERRUPT
        assert ledger.chain_valid()

    def test_awaiting_reason_code_in_audit(self, ledger: AuditLedger) -> None:
        """N2 回归：reason_code 不再死参数——SPEC_MISSING 进审计 decision 字段。"""
        svc = SessionService(InMemorySessionStore(), audit=ledger)
        sid = svc.create("u1", "t", None).id
        svc.submit_message(sid, "u1", "desc", [])
        svc.mark_awaiting_input(sid, "u1", "SPEC_MISSING")
        rows = ledger.by_session(sid)
        assert rows[-1].decision == "SPEC_MISSING"

    def test_deny_audit_records_no_pii(self, ledger: AuditLedger) -> None:
        """越权拒绝写审计，但 payload 不含资源内容（§7 合规）。"""
        row = ledger.append(
            "u2", "s1", AuditActions.DENY, "FORBIDDEN", {"actor": "u2"}, "t3"
        )
        assert row.action == AuditActions.DENY
        assert "session" not in str(row.payload).lower()

    def test_cross_user_access_writes_deny_audit(self, ledger: AuditLedger) -> None:
        """S1 收尾：真实越权路径（B 读 A 快照）自动写 DENY 审计（§7）。"""
        from services.control_plane.api.guard import ForbiddenError

        svc = SessionService(InMemorySessionStore(), audit=ledger)
        sid = svc.create("user-a", "t", None).id
        with pytest.raises(ForbiddenError):
            svc.snapshot(sid, "user-b")
        rows = ledger.by_session(sid)
        assert len(rows) == 1
        assert rows[0].action == AuditActions.DENY
        assert rows[0].decision == "FORBIDDEN"
        # DENY payload 不含资源内容，仅标识尝试者
        assert "session" not in str(rows[0].payload).lower()
