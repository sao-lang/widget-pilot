"""M07 接线断点 B1/B2/B3 单测（v1.6）。

B2：ValidationResult 建议档位/证据引用回注 State.spec；
B3：澄清回答回注 State.clarification_answer（防假续跑）；
B1：preferred_grids 从偏好 Store 读高置信值（领域适配层已测，此处测回调签名存在）。
模型用 FakeModel（本机无真实端点，接线验证非模型能力验证）。
"""
from __future__ import annotations

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.types import Input
from packages.contracts.enums import ModelTier
from services.orchestrator.guardrail_adapter import build_validator
from services.orchestrator.memory.adapters import high_confidence_grids
from services.orchestrator.memory.types import (
    FieldEvidence,
    PreferenceField,
    PreferenceSource,
    UserPreference,
    utc_now_iso,
)


def test_b2_model_tier_and_evidence_reach_state(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
) -> None:
    """B2：Input.model_tier/evidence_ref 首轮回注 State.spec。"""
    deps = Deps(
        model=fake_model, checkpoint_store=checkpoint_store,
        idempotency_store=idem_store, event_bus=event_bus,
        clock=clock, tracer=tracer, lease_provider=lease_provider,
    )
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END"}'])
    state = run(
        "s-b2", "run-b2",
        Input(
            text="做一个日历",
            model_tier=ModelTier.FRONTIER,
            evidence_ref="snap-hash-123",
        ),
        deps,
    )
    assert state.spec.tier is ModelTier.FRONTIER
    assert state.spec.evidence == "snap-hash-123"


def test_b3_clarification_answer_reaches_state(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
) -> None:
    """B3：Input.clarification_answer 回注 State（任何轮次）。"""
    deps = Deps(
        model=fake_model, checkpoint_store=checkpoint_store,
        idempotency_store=idem_store, event_bus=event_bus,
        clock=clock, tracer=tracer, lease_provider=lease_provider,
    )
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-b3"}'])
    state = run(
        "s-b3", "run-b3",
        Input(text="继续", clarification_answer="4x4 月视图"),
        deps,
    )
    assert state.clarification_answer == "4x4 月视图"


def test_v16_memory_port_reaches_model_messages(
    fake_model, checkpoint_store, idem_store, event_bus, clock, tracer, lease_provider
) -> None:
    """v1.6：注入偏好端口后，真实偏好文本进模型消息（非占位串）。"""
    from packages.agent_core.context.ports import PreferencePort

    class MockPreferencePort:
        """测试用偏好端口替身（显式注入）。"""

        def injection_text(self, user_id: str, max_tokens: int) -> str:
            """返回一条高置信偏好文本。"""
            assert user_id == "u-mem"
            return "preferred_grids: 4x4 (来源:explicit)"

    assert isinstance(MockPreferencePort(), PreferencePort)

    deps = Deps(
        model=fake_model, checkpoint_store=checkpoint_store,
        idempotency_store=idem_store, event_bus=event_bus,
        clock=clock, tracer=tracer, lease_provider=lease_provider,
    )
    fake_model.set_script(['{"signal": "FAILED", "reason": "TEST_END", "evidence": "ev-mem"}'])
    run(
        "s-mem", "run-mem",
        Input(
            text="做一个日历",
            preference_port=MockPreferencePort(),
            context_user_id="u-mem",
        ),
        deps,
    )
    contents = [m.content for m in fake_model.calls[0]["messages"]]
    assert any("preferred_grids: 4x4" in c for c in contents), "偏好文本须进 prompt"


def test_b1_high_confidence_grids_gate() -> None:
    """B1：低置信 preferred_grids 不注入校验链（D-11 门禁）。"""
    pref = UserPreference(user_id="u1")
    low = FieldEvidence(
        value=["4x4"], source=PreferenceSource.ADOPTED_ARTIFACT,
        weight=0.3, evidence_count=1, updated_at=utc_now_iso(),
    )
    pref.fields[PreferenceField.PREFERRED_GRIDS] = low
    assert high_confidence_grids(pref) == []
    high = FieldEvidence(
        value=["5x4"], source=PreferenceSource.EXPLICIT,
        weight=1.0, evidence_count=1, updated_at=utc_now_iso(),
    )
    pref.fields[PreferenceField.PREFERRED_GRIDS] = high
    assert high_confidence_grids(pref) == ["5x4"]


def test_build_validator_accepts_preferred_grids_get() -> None:
    """B1：build_validator 接受 preferred_grids_get 回调（签名契约）。"""
    import inspect

    sig = inspect.signature(build_validator)
    assert "preferred_grids_get" in sig.parameters
