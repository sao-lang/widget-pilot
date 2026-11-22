"""ADR-009 多轮事实摘要的 dispatcher 接线测试（读注入 + 写落库）。

只验证新增接线：Input.fact_summary 来自 store、DONE 终态写新事实；
其余校验/状态机路径由既有 test_m06_wiring 覆盖，不重复。
"""
from __future__ import annotations

from packages.agent_core.injection import Deps
from packages.agent_core.types import Input, State, Terminal, TerminalKind
from packages.contracts.enums import SessionState
from packages.contracts.types import TaskFields
from services.control_plane.sessions.service import SessionService
from services.guardrails.types import GridSpec, ValidationResult
from services.orchestrator import dispatcher as disp_module
from services.orchestrator.dispatcher import Dispatcher
from tests.conftest import (
    FakeClock,
    FakeLeaseProvider,
    InMemoryCheckpointStore,
    InMemoryEventBus,
    InMemoryIdempotencyStore,
)
from tests.support import AuditLedger, InMemorySessionStore
from tests.support.m07_stores import InMemoryFactSummaryStore


class _OneShotQueue:
    """仅吐一条任务的 fake 队列（不重投）。"""

    def __init__(self, task: dict[str, object]) -> None:
        self._task = task
        self.acked = False

    def consume(self, batch: int) -> list[dict[str, object]]:
        task, self._task = self._task, {}  # type: ignore[assignment]
        return [task] if task else []

    def ack(self, task: object) -> None:
        self.acked = True

    def dead_letter(self, task: object) -> None:
        raise AssertionError("unexpected dead letter")


def _dispatcher(fact_store: InMemoryFactSummaryStore, queue: _OneShotQueue,
                sessions: SessionService, fake_run: object) -> Dispatcher:
    """构造注入事实存储的 Dispatcher（run 被 monkeypatch 前先建 deps）。"""
    clock = FakeClock()
    deps = Deps(
        model=object(),  # type: ignore[arg-type] - run 被 fake 替换，模型不用
        checkpoint_store=InMemoryCheckpointStore(),
        idempotency_store=InMemoryIdempotencyStore(),
        event_bus=InMemoryEventBus(),
        clock=clock,
        lease_provider=FakeLeaseProvider(clock),
        owner="test",
    )

    def pass_validator(description: str, fields: TaskFields) -> ValidationResult:
        return ValidationResult(ok=True, grid=GridSpec._4X4)

    d = Dispatcher(
        queue=queue,  # type: ignore[arg-type]
        deps=deps,
        sessions=sessions,
        validator=pass_validator,
        fact_summary_store=fact_store,
    )
    assert fake_run is not None
    return d


def test_fact_summary_injected_into_input_and_appended_on_done(monkeypatch: object) -> None:
    """DONE 终态：读到的事实进 Input.fact_summary，并落一条新事实（turn+1）。"""
    sessions = SessionService(InMemorySessionStore(), audit=AuditLedger())
    sid = sessions.create("u1", "t", "4x4").id
    row = sessions.submit_message(sid, "u1", "做一个日历", [])
    run_id = row.run_id or "r1"
    fact_store = InMemoryFactSummaryStore()
    fact_store.append(sid, 1, {}, "历史事实：\n- 上一版 grid=4x4")
    queue = _OneShotQueue({
        "session_id": sid, "user_id": "u1", "run_id": run_id,
        "description": "改成农历", "grid": "4x4",
    })

    captured: list[str] = []

    def fake_run(session_id: str, run_id: str, inp: Input | str, deps: Deps,
                 cost_sink: object = None) -> State:
        captured.append(getattr(inp, "fact_summary", ""))
        state = State.initial(session_id, run_id, "", grid="4x4")
        return state.bump(
            terminal=Terminal(kind=TerminalKind.DONE, bundle_ref="b-2")
        )

    monkeypatch.setattr(disp_module, "run", fake_run)  # type: ignore[attr-defined]
    dispatcher = _dispatcher(fact_store, queue, sessions, fake_run)
    dispatcher.process_one()

    assert captured == ["历史事实：\n- 上一版 grid=4x4"]
    assert fact_store.latest(sid)[0] == 2  # 生成了 turn 2 事实
    assert "改成农历" in fact_store.latest(sid)[1]
    assert sessions.get(sid).state is SessionState.COMPLETED


def test_failed_turn_does_not_write_fact(monkeypatch: object) -> None:
    """ADR-009：失败 turn 不生成新事实（保留上一版），会话转 FAILED。"""
    from packages.agent_core.types import Terminal, TerminalKind
    from packages.contracts.enums import ReasonCode

    sessions = SessionService(InMemorySessionStore(), audit=AuditLedger())
    sid = sessions.create("u1", "t", "4x4").id
    row = sessions.submit_message(sid, "u1", "改农历", [])
    fact_store = InMemoryFactSummaryStore()
    fact_store.append(sid, 1, {}, "历史事实：\n- 上一版 turn=1")
    queue = _OneShotQueue({
        "session_id": sid, "user_id": "u1", "run_id": row.run_id or "r2",
        "description": "改农历", "grid": "4x4",
    })

    def fake_run_fail(session_id: str, run_id: str, inp: Input | str, deps: Deps,
                      cost_sink: object = None) -> State:
        state = State.initial(session_id, run_id, "", grid="4x4")
        return state.bump(terminal=Terminal(
            kind=TerminalKind.FAILED, reason_code=ReasonCode.RETRY_EXHAUSTED,
            evidence_ref="evidence/failed-turn",
        ))

    monkeypatch.setattr(disp_module, "run", fake_run_fail)  # type: ignore[attr-defined]
    dispatcher = _dispatcher(fact_store, queue, sessions, fake_run_fail)
    dispatcher.process_one()

    assert fact_store.latest(sid) == (1, "历史事实：\n- 上一版 turn=1")  # 未写新事实
    assert sessions.get(sid).state is SessionState.FAILED


def test_fact_store_read_failure_is_tolerated(monkeypatch: object) -> None:
    """Blocker 修复验证：PG 读故障 → fact_summary 为空串，任务仍完成。"""
    class BrokenStore(InMemoryFactSummaryStore):
        def latest(self, session_id: str) -> tuple[int, str]:
            raise RuntimeError("PG read failed")

    sessions = SessionService(InMemorySessionStore(), audit=AuditLedger())
    sid = sessions.create("u1", "t", "4x4").id
    row = sessions.submit_message(sid, "u1", "做一个日历", [])
    queue = _OneShotQueue({
        "session_id": sid, "user_id": "u1", "run_id": row.run_id or "r1",
        "description": "做一个日历", "grid": "4x4",
    })
    captured: list[str] = []

    def fake_run(session_id: str, run_id: str, inp: Input | str, deps: Deps,
                 cost_sink: object = None) -> State:
        captured.append(getattr(inp, "fact_summary", ""))
        state = State.initial(session_id, run_id, "", grid="4x4")
        return state.bump(terminal=Terminal(kind=TerminalKind.DONE, bundle_ref="b-1"))

    monkeypatch.setattr(disp_module, "run", fake_run)  # type: ignore[attr-defined]
    dispatcher = _dispatcher(BrokenStore(), queue, sessions, fake_run)
    dispatcher.process_one()
    assert captured == [""]  # 读失败容错为空，不阻断
    assert sessions.get(sid).state is SessionState.COMPLETED
