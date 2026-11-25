"""P1 生产接线 RED 测试（确定性通道 → dispatcher 分流）。

验证 Dispatcher 在 validation.ok 后、run() 前按确定性通道结果分流：
- compiled：不进 run()（model.calls==0），会话 COMPLETED + bundle_ref 落库，后置义务不丢
- llm_fallback：继续 run()
- rejected/clarification：会话 FAILED / AWAITING_INPUT，不进 run()
- 开关 off 或未注入：与现状逐字一致

先写、先跑 RED（Dispatcher 不接受 deterministic_runner 参数），再实现转 GREEN。
"""
from __future__ import annotations

from packages.agent_core.injection import Deps
from packages.agent_core.types import JsonObject, ModelOutput, Usage
from packages.contracts.enums import SessionState
from packages.contracts.types import TaskFields
from services.control_plane.sessions.service import SessionService
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
from tests.support import AuditLedger, InMemorySessionStore


def _pass_validator(description: str, fields: TaskFields) -> ValidationResult:
    return ValidationResult(ok=True, grid=GridSpec._4X4)


def _setup(description: str = "做一个任务清单"):
    store = InMemorySessionStore()
    svc = SessionService(store, audit=AuditLedger())
    row = svc.create("u1", "t", "4x4")
    submitted = svc.submit_message(row.id, "u1", description, [])

    class _FakeQueue:
        def __init__(self) -> None:
            self.acked = 0
            self.dead = 0

        def consume(self, batch: int) -> list[dict[str, str]]:
            return [{
                "session_id": row.id, "user_id": "u1",
                "run_id": submitted.run_id or "", "description": description,
            }]

        def ack(self, task: JsonObject) -> None:
            self.acked += 1

        def dead_letter(self, task: JsonObject) -> None:
            self.dead += 1

    clock = FakeClock()
    model = FakeModel()
    model.set_script([
        ModelOutput(content='{"signal":"DONE","bundle_ref":"model-b1","reason":"OK"}',
                    usage=Usage(1, 1))
    ])
    deps = Deps(
        model=model,
        checkpoint_store=InMemoryCheckpointStore(),
        idempotency_store=InMemoryIdempotencyStore(),
        event_bus=InMemoryEventBus(),
        clock=clock,
        lease_provider=FakeLeaseProvider(clock),
        owner="test",
    )
    return store, svc, row, _FakeQueue(), deps, model


def _dispatcher(queue, deps, svc, runner=None, **kwargs):
    return Dispatcher(
        queue=queue,  # type: ignore[arg-type] - 测试桩非真 RedisTaskQueue
        deps=deps, sessions=svc,
        validator=_pass_validator, deterministic_runner=runner, **kwargs,
    )


def test_compiled_hit_skips_run_and_writes_bundle() -> None:
    """compiled 命中：不进 run()（零模型），会话 COMPLETED 且 bundle_ref 落库。"""
    store, svc, row, queue, deps, model = _setup()
    calls: list[str] = []

    def runner(inp, session_id, run_id) -> dict:
        calls.append("runner")
        return {"branch": "compiled", "bundle": {"bundle_id": "det-b1"},
                "generation_path": "deterministic", "model_calls": 0}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert calls == ["runner"]
    assert len(model.calls) == 0  # 零云端 LLM
    row_after = store.get(row.id)
    assert row_after is not None and row_after.state is SessionState.COMPLETED
    assert row_after.bundle_ref == "det-b1"
    assert queue.acked == 1  # 后置义务：ACK 不丢


def test_llm_fallback_continues_run() -> None:
    """llm_fallback：继续原 run()，模型被调用，会话 COMPLETED。"""
    store, svc, row, queue, deps, model = _setup()

    def runner(inp, session_id, run_id) -> dict:
        return {"branch": "llm_fallback", "generation_path": "cloud-fallback",
                "bundle": None, "model_calls": 0}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert len(model.calls) == 1  # 交回原链路
    assert store.get(row.id).state is SessionState.COMPLETED  # type: ignore[union-attr]


def test_rejected_writes_failed_without_run() -> None:
    """rejected：会话 FAILED，不进 run()，零模型。"""
    store, svc, row, queue, deps, model = _setup()

    def runner(inp, session_id, run_id) -> dict:
        return {"branch": "rejected", "reason": "OUT_OF_SCOPE",
                "bundle": None, "model_calls": 0}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert len(model.calls) == 0
    assert store.get(row.id).state is SessionState.FAILED  # type: ignore[union-attr]
    assert queue.acked == 1


def test_switch_off_skips_runner(monkeypatch) -> None:
    """WIDGETPILOT_DETERMINISTIC=off：钩子不调用，行为与现状一致（走 run）。"""
    monkeypatch.setenv("WIDGETPILOT_DETERMINISTIC", "off")
    store, svc, row, queue, deps, model = _setup()
    calls: list[str] = []

    def runner(inp, session_id, run_id) -> dict:
        calls.append("runner")
        return {"branch": "compiled", "bundle": {"bundle_id": "det-b1"}}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert calls == []  # 开关 off，钩子零调用
    assert len(model.calls) == 1  # 原链路
    assert store.get(row.id).state is SessionState.COMPLETED  # type: ignore[union-attr]


def test_clarification_writes_awaiting_without_run() -> None:
    """clarification：会话 AWAITING_INPUT，不进 run()，追问槽位落库。"""
    store, svc, row, queue, deps, model = _setup()

    def runner(inp, session_id, run_id) -> dict:
        return {"branch": "clarification", "reason": "SPEC_MISSING:grid",
                "missing_slots": ["grid"], "bundle": None, "model_calls": 0}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert len(model.calls) == 0
    assert store.get(row.id).state is SessionState.AWAITING_INPUT  # type: ignore[union-attr]


def test_no_runner_keeps_legacy_path() -> None:
    """未注入 runner：逐字保持现状，走 run()。"""
    store, svc, row, queue, deps, model = _setup()
    dispatcher = _dispatcher(queue, deps, svc, None)
    assert dispatcher.process_one() == 1
    assert len(model.calls) == 1
    assert store.get(row.id).state is SessionState.COMPLETED  # type: ignore[union-attr]


# ---------- Fix loop（独立 review Round 1 B1-B4）----------

def test_B1_real_rejected_reason_never_crashes_terminal() -> None:
    """B1：真实 channel 的 rejected（reason 为空）不得崩 FAILED 终态。"""
    from services.deterministic.channel import run_deterministic
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    real = run_deterministic(
        "今天天气怎么样", attachments=(), bundle_service=None,  # rejected 不消费 assemble
        evidence_store=MemoryEvidenceStore(), source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=CommandResult(stdout="{}", stderr="", exit_code=0)),
        user_id="u1", session_id="sess", run_id="r1",
    )
    assert real["branch"] == "rejected"
    assert real["reason"]  # 通道已兜底非空 reason_code

    # 用真实通道结果驱动 dispatcher：不得抛 ValueError、必须 FAILED + ACK
    store, svc, row, queue, deps, model = _setup()

    def runner(inp, session_id, run_id):
        return real

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert len(model.calls) == 0
    assert store.get(row.id).state is SessionState.FAILED  # type: ignore[union-attr]
    assert queue.acked == 1


def test_B2_production_like_hash_checked_source_reaches_assemble() -> None:
    """B2：get 强制校验 #sha256 ref 的 store 下，compiled 必须真成包。"""
    import hashlib

    from services.deterministic.channel import run_deterministic
    from services.guardrails.output_ports import make_object_ref
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, MemoryEvidenceStore, sandbox_report

    def _ok_sandbox_report():
        return sandbox_report(
            bridge_events=[],
            render={"grid": "4x2", "width_px": 320, "height_px": 160,
                    "max_child_width_px": 300, "max_child_height_px": 150, "errors": []},
        )

    class _HashCheckedSource:
        """生产同构：get 要求带 hash ref 且校验，裸 key 会 KeyError。"""

        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}

        def put(self, key: str, data: bytes):
            from services.sandbox_runner.ports import ObjectRef

            ref = make_object_ref(key, data)
            self.objects[ref] = data
            return ObjectRef(key=key, hash=hashlib.sha256(data).hexdigest(), size=len(data))

        def get(self, ref: str) -> bytes:
            expected = ref.split("#sha256:", 1)[1]
            data = self.objects[ref]
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("hash mismatch")
            return data

    from services.control_plane.bundles.service import BundleService
    from tests.support.bundles import (
        InMemoryBundleStore,
        RecordingArtifactAdoption,
        RecordingCorpusLink,
        RecordingEventSink,
        RecordingPreferenceRevocation,
    )
    from tests.support.control_plane import AuditLedger

    source = _HashCheckedSource()
    svc = BundleService(
        store=InMemoryBundleStore(), audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(), preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )
    result = run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办", attachments=(),
        bundle_service=svc, evidence_store=MemoryEvidenceStore(), source_store=source,
        docker_runner=FakeDockerRunner(result=CommandResult(
            stdout=__import__("json").dumps(_ok_sandbox_report()), stderr="", exit_code=0)),
        user_id="u1", session_id="b2-prod-like", run_id="b2-run",
    )
    assert result["branch"] == "compiled", result.get("reason")
    assert result["bundle"] is not None  # assemble 真被调用
    assert result["bundle"]["bundle_id"]


def test_B3_gate_failed_never_falls_back_to_cloud_run() -> None:
    """B3：通道 failed（门禁未过）→ FAILED 终态，绝不进 run() 云端。"""
    store, svc, row, queue, deps, model = _setup()

    def runner(inp, session_id, run_id):
        return {"branch": "failed", "reason": "GUARD_OUTPUT_HIT",
                "evidence_ref": "evidence/real/failed#sha256:" + "9" * 64,
                "generation_path": "deterministic", "bundle": None}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert len(model.calls) == 0  # 门禁失败绝不调云端
    assert store.get(row.id).state is SessionState.FAILED  # type: ignore[union-attr]
    assert store.get(row.id).evidence_ref == "evidence/real/failed#sha256:" + "9" * 64  # type: ignore[union-attr]


def test_B4_clarification_grid_answer_reaches_channel() -> None:
    """B4：runner 闭包真把 Input.grid/回答并入描述，规则层收敛（非走过场测试）。"""
    # 两条真实重入路径：grid 字段 / clarification_answer 字段
    from packages.agent_core.types import Input
    from services.guardrails.interpreter.rule import RuleInterpreter
    from services.orchestrator.deterministic_runner import (
        merge_clarification_into_description,
    )

    for inp in (
        Input(text="桌面上加个卡片显示待办", grid="4x4"),
        Input(text="桌面上加个卡片显示待办", clarification_answer="4x4"),
    ):
        merged = merge_clarification_into_description(inp)
        assert "4x4" in merged, f"runner 未并入澄清答案: {merged}"
        result = RuleInterpreter().interpret(merged)
        assert result.kind == "compiled", merged
        assert result.instances[0].slots["grid"] == "4x4"


# ---------- P1 清理 #37：命中分支成本账 ----------

def test_compiled_hit_calls_cost_sink() -> None:
    """命中确定性分支跳过 run() 时仍须落成本账（零成本但留痕）。"""
    _store, svc, _row, queue, deps, _model = _setup()
    cost_calls: list[str] = []

    def cost_sink(state) -> None:
        cost_calls.append(state.run_id)

    def runner(inp, session_id, run_id):
        return {"branch": "compiled", "bundle": {"bundle_id": "det-b1"},
                "generation_path": "deterministic", "model_calls": 0}

    dispatcher = _dispatcher(queue, deps, svc, runner, cost_sink=cost_sink)
    assert dispatcher.process_one() == 1
    assert cost_calls, "命中 compiled 分支未落成本账"


def test_llm_fallback_cost_sink_not_double_called() -> None:
    """llm_fallback 走 run() 内部落账一次，dispatcher 不得再调第二次。"""
    _store, svc, _row, queue, deps, _model = _setup()
    cost_calls: list[str] = []

    def cost_sink(state) -> None:
        cost_calls.append(state.run_id)

    def runner(inp, session_id, run_id):
        return {"branch": "llm_fallback", "generation_path": "cloud-fallback",
                "bundle": None}

    dispatcher = _dispatcher(queue, deps, svc, runner, cost_sink=cost_sink)
    assert dispatcher.process_one() == 1
    assert len(cost_calls) == 1, f"成本账重复落账: {cost_calls}"


# ---------- P1 清理 #42：命中分支终态事件 ----------

def test_compiled_hit_emits_terminal_event() -> None:
    """命中 compiled 分支须发终态事件（前端 SSE 据此关闭 loading）。"""
    _store, svc, _row, queue, deps, _model = _setup()
    emitted: list[str] = []

    def emitter(state) -> None:
        emitted.append(state.terminal.kind.value if state.terminal else "no-terminal")

    def runner(inp, session_id, run_id):
        return {"branch": "compiled", "bundle": {"bundle_id": "det-b1"},
                "generation_path": "deterministic"}

    dispatcher = _dispatcher(queue, deps, svc, runner, terminal_event_emitter=emitter)
    assert dispatcher.process_one() == 1
    assert emitted == ["DONE"], f"命中 compiled 未发终态事件: {emitted}"


def test_clarification_hit_emits_event() -> None:
    """命中 clarification 分支须发澄清事件（前端据此弹追问）。"""
    _store, svc, _row, queue, deps, _model = _setup()
    emitted: list[str] = []

    def emitter(state) -> None:
        emitted.append(state.phase.value)

    def runner(inp, session_id, run_id):
        return {"branch": "clarification", "reason": "SPEC_MISSING:grid",
                "bundle": None, "generation_path": "deterministic"}

    dispatcher = _dispatcher(queue, deps, svc, runner, terminal_event_emitter=emitter)
    assert dispatcher.process_one() == 1
    assert emitted == ["AWAITING_INPUT"], f"澄清未发事件: {emitted}"


# ---------- P1 清理 #37：reason_code 归一 ----------

def test_reason_code_normalized_for_failed_branch() -> None:
    """通道自由字符串 GATE_FAILED 归一到 ReasonCode，不落非枚举值。"""
    from packages.contracts.enums import ReasonCode
    from services.orchestrator.dispatcher import _normalize_reason

    assert _normalize_reason("GATE_FAILED", "failed") == ReasonCode.GUARD_OUTPUT_HIT.value
    assert _normalize_reason("COMPILE_FAILED", "failed") == ReasonCode.COMPILE_FAILED.value
    assert _normalize_reason("", "rejected") == ReasonCode.INTENT_OUT_OF_SCOPE.value
    assert _normalize_reason("", "failed") == ReasonCode.GUARD_OUTPUT_HIT.value


def test_failed_branch_writes_enum_reason() -> None:
    """端到端：通道 GATE_FAILED → 会话落库 reason_code 是枚举值。"""
    from packages.contracts.enums import ReasonCode

    store, svc, row, queue, deps, _model = _setup()

    def runner(inp, session_id, run_id):
        return {"branch": "failed", "reason": "GATE_FAILED",
                "bundle": None, "generation_path": "deterministic"}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    assert store.get(row.id).reason_code == ReasonCode.GUARD_OUTPUT_HIT.value  # type: ignore[union-attr]


# ---------- P1 收尾 ADR-019 D4/D5/#38：LLM 产物存在性校验 ----------

def test_llm_unverified_bundle_does_not_complete_session() -> None:
    """模型自报不存在的 bundle_id 不得置 DONE（no fake green）。"""
    from packages.contracts.enums import SessionState

    _store, svc, row, queue, deps, _model = _setup()
    verifier_calls: list[tuple[str, str]] = []

    def verifier(bundle_id: str, user_id: str) -> bool:
        verifier_calls.append((bundle_id, user_id))
        return False  # 模型自报的 model-b1 不存在

    dispatcher = _dispatcher(queue, deps, svc, None, artifact_verifier=verifier)
    assert dispatcher.process_one() == 1
    assert verifier_calls == [("model-b1", "u1")]
    assert svc.get(row.id).state is not SessionState.COMPLETED
    assert svc.get(row.id).state is SessionState.FAILED


def test_llm_verified_bundle_completes_session() -> None:
    """真实存在的 bundle_id 才置 DONE。"""
    from packages.contracts.enums import SessionState

    store, svc, row, queue, deps, _model = _setup()

    def verifier(bundle_id: str, user_id: str) -> bool:
        return bundle_id == "model-b1" and user_id == "u1"

    dispatcher = _dispatcher(queue, deps, svc, None, artifact_verifier=verifier)
    assert dispatcher.process_one() == 1
    assert svc.get(row.id).state is SessionState.COMPLETED
    _row = store.get(row.id)
    assert _row is not None and _row.bundle_ref == "model-b1"


# ---------- ADR-019 D2/D3：generation_path 埋点与 occurrence ----------

def test_deterministic_hit_records_path_and_occurrence() -> None:
    """命中 compiled：path 进 contextvar + occurrence sink 收到 (session,run,path,branch)。"""
    from services.orchestrator.request_context import generation_path_var

    _store, svc, row, queue, deps, _model = _setup()
    occurrences: list[tuple[str, str, str, str]] = []
    seen_paths: list[str] = []

    def runner(inp, session_id, run_id):
        return {"branch": "compiled", "bundle": {"bundle_id": "det-b1"},
                "generation_path": "deterministic"}

    def sink(state) -> None:
        seen_paths.append(generation_path_var.get())

    dispatcher = _dispatcher(
        queue, deps, svc, runner,
        occurrence_sink=lambda *args: occurrences.append(args),
        cost_sink=sink,
    )
    assert dispatcher.process_one() == 1
    assert seen_paths == ["deterministic"]  # cost_sink 在请求内读到真实值
    assert generation_path_var.get() == "llm-only"  # 请求结束已复位，不串号
    assert len(occurrences) == 1
    sid, rid, path, branch = occurrences[0]
    assert sid == row.id and rid  # run_id 由 task 分配，非空即可
    assert (path, branch) == ("deterministic", "compiled")


def test_llm_fallback_records_cloud_fallback() -> None:
    """llm_fallback：path=cloud-fallback 进 contextvar + occurrence 留证。"""
    from services.orchestrator.request_context import generation_path_var

    _store, svc, _row, queue, deps, _model = _setup()
    occurrences: list[tuple[str, str, str, str]] = []
    seen_paths: list[str] = []

    def runner(inp, session_id, run_id):
        return {"branch": "llm_fallback", "generation_path": "cloud-fallback",
                "bundle": None}

    def sink(state) -> None:
        seen_paths.append(generation_path_var.get())

    dispatcher = _dispatcher(
        queue, deps, svc, runner,
        occurrence_sink=lambda *args: occurrences.append(args),
        cost_sink=sink,
    )
    assert dispatcher.process_one() == 1
    assert "cloud-fallback" in seen_paths  # run() 内 cost_sink 读到真实值
    assert generation_path_var.get() == "llm-only"  # 复位
    assert occurrences[0][2] == "cloud-fallback"


# ---------- P2 复审 B2：目标反问精准信息不丢 ----------

def test_target_choices_reach_session_evidence() -> None:
    """定位不明 → choices 落到 SessionRow.evidence_ref（前端可回读精准信息）。"""
    import json

    _store, svc, row, queue, deps, _model = _setup()
    choices = [
        {"bundle_id": "b1", "grid": "4x2", "host": "桌面",
         "data": "日历", "created_at": "2026-11-25T10:00:00+00:00"},
    ]

    def runner(inp, session_id, run_id):
        return {
            "branch": "clarification", "reason": "TARGET_AMBIGUOUS",
            "question_kind": "target", "target_choices": choices,
            "missing_slots": ["target_bundle"], "generation_path": "deterministic",
        }

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    session_row = svc.get(row.id)
    # N2（既有设计）：reason_code 只进审计不落 Session 主记录；精准信息走
    # evidence_ref（已落库、前端可回读），reason 也在 payload 中。
    assert session_row.evidence_ref
    payload = json.loads(session_row.evidence_ref)
    assert payload["reason"] == "TARGET_AMBIGUOUS"
    assert payload["question_kind"] == "target"
    assert payload["choices"][0]["bundle_id"] == "b1"


# ---------- P0 N>1：dispatcher 命中多 bundle 不丢 ----------

def test_deterministic_multi_bundles_complete_with_first() -> None:
    """N 个 bundle 都成包入库，session.bundle_ref 指向首个（单值契约兼容）。"""
    from packages.contracts.enums import SessionState

    _store, svc, row, queue, deps, _model = _setup()
    bundles = [
        {"bundle_id": "multi-b1"}, {"bundle_id": "multi-b2"},
    ]

    def runner(inp, session_id, run_id):
        return {"branch": "compiled", "bundle": bundles[0], "bundles": bundles,
                "generation_path": "deterministic"}

    dispatcher = _dispatcher(queue, deps, svc, runner)
    assert dispatcher.process_one() == 1
    session_row = svc.get(row.id)
    assert session_row.state is SessionState.COMPLETED
    assert session_row.bundle_ref == "multi-b1"


# ---------- P0 返工④：沙箱耗时回灌成本账 ----------

def test_deterministic_cost_sink_gets_real_sandbox_minutes() -> None:
    """命中分支把 validate_and_test 真耗时写进 budget_used（成本账不再恒 0）。"""
    seen: list[dict] = []

    _store, svc, _row, queue, deps, _model = _setup()

    def runner(inp, session_id, run_id):
        return {"branch": "compiled", "bundle": {"bundle_id": "b1"},
                "bundles": [{"bundle_id": "b1"}], "generation_path": "deterministic",
                "elapsed_ms": 30_000}

    def sink(state) -> None:
        seen.append(dict(state.budget_used))

    dispatcher = _dispatcher(queue, deps, svc, runner, cost_sink=sink)
    assert dispatcher.process_one() == 1
    assert seen and seen[0]["sandbox_minutes"] == 0.5
