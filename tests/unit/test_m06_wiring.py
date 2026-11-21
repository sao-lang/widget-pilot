"""M06 接线义务回归测试（2026-11-21 接线核查）。

锁定此前未接的核心义务：
- C1：AttachmentView 字段名 role_confidence 正名（原 confidence）；
- A4：mark_awaiting_input 支持 evidence_ref 落库（fail 原已支持）；
- A1：Dispatcher.process_one 在 run() 前过 validate_chain，命中即不放行、回写证据。

validator / store 均为 fake 或内存实现，验证「接线是否接通」而非模型能力，
与 M06 §6.2.1 的 mock 桩策略一致（真模型属排除项）。
"""

from __future__ import annotations

import pytest

from packages.agent_core.injection import Deps
from packages.agent_core.types import Input, JsonObject, State
from packages.contracts.enums import ReasonCode, SessionState
from packages.contracts.types import TaskFields
from services.control_plane.sessions.service import SessionService
from services.guardrails.types import AttachmentView
from tests.support import AuditLedger, InMemorySessionStore


class TestC1RoleConfidenceNaming:
    """C1：AttachmentView 使用 role_confidence 正名字段。"""

    def test_view_uses_role_confidence(self) -> None:
        """字段正名：可用 role_confidence 构造与读取。"""
        att = AttachmentView(
            attachment_id="a1",
            role="visual_reference",
            role_confidence=0.9,
            insight_ref="ir",
        )
        assert att.role_confidence == 0.9


class TestA4EvidenceRefWriteback:
    """A4：fail / mark_awaiting_input 落 evidence_ref。"""

    def test_fail_writes_evidence_ref(self) -> None:
        """fail 传入的 evidence_ref 落到会话行（已有能力，锁定防回归）。"""
        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "t", "4x4")
        svc.submit_message(row.id, "u1", "做一个日历", [])
        svc.fail(row.id, ReasonCode.INPUT_INVALID.value, "evidence/s1/r1#sha256:abc")
        assert store.get(row.id).evidence_ref == "evidence/s1/r1#sha256:abc"  # type: ignore[union-attr]

    def test_mark_awaiting_writes_evidence_ref(self) -> None:
        """mark_awaiting_input 传入 evidence_ref 时落到会话行（本轮新接）。"""
        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "t", "4x4")
        svc.submit_message(row.id, "u1", "做一个日历", [])
        svc.mark_awaiting_input(
            row.id,
            "u1",
            ReasonCode.SPEC_MISSING.value,
            evidence_ref="evidence/s1/r1#sha256:def",
        )
        assert store.get(row.id).evidence_ref == "evidence/s1/r1#sha256:def"  # type: ignore[union-attr]


class TestA1DispatcherWiring:
    """A1：Dispatcher 过 validate_chain 后按结论分支回写。"""

    def test_dispatcher_calls_validator_before_run(self) -> None:
        """注入 validator 后：process_one 调 validator，命中即 fail 不跑 run。"""
        from services.control_plane.sessions.service import SessionService
        from services.guardrails.types import ValidationResult
        from services.orchestrator.dispatcher import Dispatcher
        from tests.conftest import (
            FakeClock,
            FakeModel,
            InMemoryEventBus,
            InMemoryIdempotencyStore,
        )

        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "t", "4x4")
        submitted = svc.submit_message(row.id, "u1", "注入攻击描述", [])

        class _FakeQueue:
            def consume(self, batch: int) -> list[dict[str, str]]:
                """返回一次任务。"""
                return [
                    {
                        "session_id": row.id,
                        "user_id": "u1",
                        "run_id": submitted.run_id or "",
                        "description": "注入攻击描述",
                    }
                ]

            def ack(self, task: JsonObject) -> None:
                """空 ACK。"""
                return

            def dead_letter(self, task: JsonObject) -> None:
                """空死信。"""
                return

        calls: list[JsonObject] = []

        def fake_validator(
            description: str, fields: TaskFields
        ) -> ValidationResult:
            """命中注入：返回 GUARD_INPUT_HIT 终态（不放行进模型）。"""
            calls.append({"description": description, "session_id": fields["session_id"]})
            return ValidationResult(
                ok=False,
                reason_code=ReasonCode.GUARD_INPUT_HIT,
                evidence_ref="evidence/s1/r1#sha256:hit",
            )

        model = FakeModel()
        deps = Deps(
            model=model,
            checkpoint_store=None,  # type: ignore[arg-type]
            idempotency_store=InMemoryIdempotencyStore(),
            event_bus=InMemoryEventBus(),
            clock=FakeClock(),
            lease_provider=None,  # type: ignore[arg-type]
            owner="test",
        )
        dispatcher = Dispatcher(
            queue=_FakeQueue(),  # type: ignore[arg-type]
            deps=deps,
            sessions=svc,
            validator=fake_validator,
        )
        assert dispatcher.process_one() == 1
        assert len(calls) == 1
        assert store.get(row.id).state is SessionState.FAILED  # type: ignore[union-attr]
        assert (
            store.get(row.id).evidence_ref == "evidence/s1/r1#sha256:hit"  # type: ignore[union-attr]
        )

    def test_clarification_keeps_original_description_for_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R3-1（终审）：澄清重入时 run() 收到的 Input.text 是原始需求，不是简短 answer。

        若误用 answer（如"四行四列"），模型只收到规格而丢失控件/特征需求。
        """
        import services.orchestrator.dispatcher as disp_module
        from services.guardrails.types import GridSpec, ValidationResult
        from services.orchestrator.dispatcher import Dispatcher
        from tests.conftest import (
            FakeClock,
            FakeLeaseProvider,
            FakeModel,
            InMemoryCheckpointStore,
            InMemoryEventBus,
            InMemoryIdempotencyStore,
        )

        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "t", "4x4")
        submitted = svc.submit_message(row.id, "u1", "做一个能翻月的日历", [])
        # 模拟澄清已受理：会话转 AWAITING_INPUT 后回答，original_description 保留
        row = store.get(row.id)  # type: ignore[assignment]
        assert row is not None
        row.state = SessionState.AWAITING_INPUT
        store.update(row)
        svc.answer_clarification(row.id, "u1", "q1", "四行四列")

        class _FakeQueue:
            def consume(self, batch: int) -> list[JsonObject]:
                """澄清重入任务：description 是原始需求（server 用 original_description 填）。"""
                return [
                    {
                        "session_id": row.id,
                        "user_id": "u1",
                        "run_id": submitted.run_id or "",
                        "description": "做一个能翻月的日历",  # 原始描述，非 answer
                        "grid": "4x4",
                        "clarified_grid": "4x4",
                        "clarification_reply": "1",
                    }
                ]

            def ack(self, task: JsonObject) -> None:
                """空 ACK。"""
                return

            def dead_letter(self, task: JsonObject) -> None:
                """空死信。"""
                return

        def pass_validator(
            description: str, fields: TaskFields
        ) -> ValidationResult:
            """放行。"""
            return ValidationResult(ok=True, grid=GridSpec._4X4)

        captured: list[str] = []

        def fake_run(
            session_id: str,
            run_id: str,
            inp: Input | str,
            deps: Deps,
            cost_sink: object = None,
        ) -> State:
            """捕获 run() 收到的 Input.text（cost_sink 为 v1.6 新增注入点）。"""
            captured.append(str(getattr(inp, "text", "")))
            return State.initial(session_id, run_id, "", grid="4x4")

        monkeypatch.setattr(disp_module, "run", fake_run)
        clock = FakeClock()
        deps = Deps(
            model=FakeModel(),
            checkpoint_store=InMemoryCheckpointStore(),
            idempotency_store=InMemoryIdempotencyStore(),
            event_bus=InMemoryEventBus(),
            clock=clock,
            lease_provider=FakeLeaseProvider(clock),
            owner="test",
        )
        dispatcher = Dispatcher(
            queue=_FakeQueue(),  # type: ignore[arg-type]
            deps=deps,
            sessions=svc,
            validator=pass_validator,
        )
        dispatcher.process_one()
        assert captured == ["做一个能翻月的日历"]
        assert "四行四列" not in captured[0]

    def test_validator_pass_lets_run_proceed(self) -> None:
        """校验放行（ok=True）时不阻断，仍进 M03 run() 完成生成。"""
        from packages.agent_core.types import ModelOutput, Usage
        from services.guardrails.types import GridSpec, ValidationResult
        from services.orchestrator.dispatcher import Dispatcher
        from tests.conftest import (
            FakeClock,
            FakeLeaseProvider,
            FakeModel,
            InMemoryCheckpointStore,
            InMemoryEventBus,
            InMemoryIdempotencyStore,
        )

        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "t", "4x4")
        submitted = svc.submit_message(row.id, "u1", "做一个日历", [])

        class _FakeQueue:
            def consume(self, batch: int) -> list[dict[str, str]]:
                """返回一次任务。"""
                return [
                    {
                        "session_id": row.id,
                        "user_id": "u1",
                        "run_id": submitted.run_id or "",
                        "description": "做一个日历",
                    }
                ]

            def ack(self, task: JsonObject) -> None:
                """空 ACK。"""
                return

            def dead_letter(self, task: JsonObject) -> None:
                """空死信。"""
                return

        def pass_validator(
            description: str, fields: TaskFields
        ) -> ValidationResult:
            """放行校验结果。"""
            return ValidationResult(ok=True, grid=GridSpec._4X4)

        clock = FakeClock()
        model = FakeModel()
        model.set_script(
            [
                ModelOutput(
                    content='{"signal":"DONE","bundle_ref":"b1","reason":"OK"}',
                    usage=Usage(1, 1),
                )
            ]
        )
        deps = Deps(
            model=model,
            checkpoint_store=InMemoryCheckpointStore(),
            idempotency_store=InMemoryIdempotencyStore(),
            event_bus=InMemoryEventBus(),
            clock=clock,
            lease_provider=FakeLeaseProvider(clock),
            owner="test",
        )
        dispatcher = Dispatcher(
            queue=_FakeQueue(),  # type: ignore[arg-type]
            deps=deps,
            sessions=svc,
            validator=pass_validator,
        )
        assert dispatcher.process_one() == 1
        # 放行路径：模型推进到 DONE → 会话 COMPLETED（未误拦）
        assert store.get(row.id).state is SessionState.COMPLETED  # type: ignore[union-attr]
