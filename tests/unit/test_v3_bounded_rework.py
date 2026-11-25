"""返工③调度层 mock 测试：只验调度不变量，不证明模型能力（追踪文档已标注）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from packages.contracts.types import JsonObject
from services.deterministic.compiler import compile_spec
from services.deterministic.rework import failure_summary, try_bound_rework
from services.guardrails.interpreter.types import SlotFill


@dataclass
class _Reason:
    value: str = "SANDBOX_TEST_FAILED"


@dataclass
class _Guard:
    violations: tuple[str, ...] = ("forbidden:eval",)


@dataclass
class _Validation:
    reason_code: _Reason
    output_guard: _Guard
    contract_check: _Guard
    sandbox_test: object | None = None


class _Port:
    def __init__(self, result: object) -> None:
        self.calls: list[tuple] = []
        self._result = result

    def rework(self, spec: JsonObject, failures: tuple[str, ...], description: str) -> JsonObject:
        self.calls.append((spec, failures, description))
        return cast(JsonObject, self._result)


def _spec():
    return compile_spec(SlotFill(
        intent_id="widget.generic", instance_id="h:0",
        slots={"grid": "4x2", "data": "待办", "host": "桌面"},
    ))


def test_off_never_calls_port() -> None:
    """rework_llm=off 零调用（ADR §6：off 时零 model.complete）。"""
    port = _Port(_spec())
    assert try_bound_rework(_spec(), _Validation(_Reason(), _Guard(), _Guard()),
                            "desc", port, rework_llm="off") is None
    assert port.calls == []


def test_on_calls_once_with_evidence() -> None:
    """on 时最多一次，失败证据与描述进 port；输出合法 spec 返回。"""
    fixed = _spec()
    port = _Port(fixed)
    result = try_bound_rework(
        fixed, _Validation(_Reason(), _Guard(), _Guard()), "改成 4x4", port,
        rework_llm="on",
    )
    assert result is not None
    assert len(port.calls) == 1
    failures = port.calls[0][1]
    assert "SANDBOX_TEST_FAILED" in failures
    assert any("forbidden:eval" in f for f in failures)
    assert port.calls[0][2] == "改成 4x4"


def test_on_without_port_fails_closed() -> None:
    """on 但无端口 → None（不静默降级为原 spec 假装成功）。"""
    assert try_bound_rework(_spec(), _Validation(_Reason(), _Guard(), _Guard()),
                            "desc", None, rework_llm="on") is None


def test_illegal_model_output_rejected() -> None:
    """模型输出过不了 schema → 拒绝（不可信数据不进产物）。"""
    port = _Port({"garbage": True})
    assert try_bound_rework(_spec(), _Validation(_Reason(), _Guard(), _Guard()),
                            "desc", port, rework_llm="on") is None


def test_failure_summary_includes_sandbox() -> None:
    sandbox = type("S", (), {"passed": False, "reason_code": "COMPILE_FAILED"})()
    v = _Validation(_Reason(), _Guard(), _Guard(), sandbox)
    assert any("COMPILE_FAILED" in f for f in failure_summary(v))


# ---------- channel 集成：返工③端到端（mock 端口，零真模型） ----------

def _channel_setup():
    import tests.unit.test_v3_deterministic_channel as ch
    from tests.support.m08 import FakeSourceStore, MemoryEvidenceStore

    def _runner_bridge(ok_report):
        return ch._command_result(ok_report)

    return ch, FakeSourceStore({}), MemoryEvidenceStore()


def _fake_port_returns_same_spec():
    """mock 端口：返回原 spec（证据回填与调用次数在调度层测过，这里测门禁重过）。"""
    class _Port:
        def __init__(self):
            self.calls = 0

        def rework(self, spec: JsonObject, failures: tuple[str, ...], description: str) -> JsonObject:
            self.calls += 1
            return spec  # 内容不变；门禁通过与否由 runner 决定

    return _Port()


def test_channel_off_never_calls_rework_port() -> None:
    """off：即使门禁失败也零返工调用，branch=failed。"""
    import tests.unit.test_v3_deterministic_channel as ch
    from services.deterministic.channel import run_deterministic
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    port = _fake_port_returns_same_spec()
    result = run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        bundle_service=ch._svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=CommandResult(stdout="{}", stderr="", exit_code=1)),
        user_id="u1", session_id="s", run_id="rework-off",
        rework_llm="off", rework_llm_port=port,
    )
    assert port.calls == 0
    assert result["branch"] == "failed"
    assert result["model_calls"] == 0


def test_channel_on_reworks_once_and_passes_same_gate() -> None:
    """on：首次门禁失败 → mock 返工一次 → 第二次过同款门禁 → llm-rework 标注。"""
    import tests.unit.test_v3_deterministic_channel as ch
    from services.deterministic.channel import run_deterministic
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    port = _fake_port_returns_same_spec()

    class _Flaky(FakeDockerRunner):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def run(self, cmd, *, stdin, timeout_s):
            self.calls += 1
            if self.calls == 1:
                return CommandResult(stdout="{}", stderr="first fail", exit_code=1)
            return ch._command_result(ch._ok_sandbox_report())

    result = run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        bundle_service=ch._svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}), docker_runner=_Flaky(),
        user_id="u1", session_id="s", run_id="rework-on",
        rework_llm="on", rework_llm_port=port,
    )
    assert port.calls == 1  # 有界：最多一次
    assert result["branch"] == "compiled"
    assert result["generation_path"] == "llm-rework"
    assert result["model_calls"] == 1


# ---------- P2/P5 重成包：slot 继承 ----------

def test_merge_rework_slots_inherits_base_and_overrides_new_grid() -> None:
    """新 grid 覆盖旧值；data/host 从目标 spec 继承（零模型文本反推）。"""
    from services.deterministic.rework import merge_rework_slots
    from services.guardrails.interpreter.types import SlotFill

    base = {
        "grid": "2x2",
        "intent": "桌面日历",
        "layout_slots": [{"slot": "main", "content": "日期网格"}],
    }
    fill = SlotFill(
        intent_id="WIDGET_MODIFY", instance_id="h:0",
        slots={"grid": "4x4"},  # 用户只说了新 grid
    )
    merged = merge_rework_slots(base, fill)
    assert merged.slots["grid"] == "4x4"
    assert merged.slots["data"]  # 从旧 spec 反推
    assert merged.slots["host"] == "桌面"
    assert merged.intent_id == "WIDGET_MODIFY"


def test_merge_rework_slots_keeps_explicit_new_data() -> None:
    """用户明确给的新 data 不被旧 spec 覆盖。"""
    from services.deterministic.rework import merge_rework_slots
    from services.guardrails.interpreter.types import SlotFill

    base = {"grid": "2x2", "intent": "桌面日历",
            "layout_slots": [{"slot": "main", "content": "日期网格"}]}
    fill = SlotFill(intent_id="WIDGET_MODIFY", instance_id="h:0",
                    slots={"grid": "4x4", "data": "待办"})
    assert merge_rework_slots(base, fill).slots["data"] == "待办"


def test_channel_rework_uses_candidate_spec_not_original() -> None:
    """N2：产物 spec 必须来自 candidate（而非原 spec），否则返工是假接线。"""
    import copy

    import tests.unit.test_v3_deterministic_channel as ch
    from services.deterministic.channel import run_deterministic
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    class _CandidatePort:
        def __init__(self):
            self.calls = 0

        def rework(self, spec, failures, description):
            self.calls += 1
            candidate = copy.deepcopy(spec)
            dc = candidate.get("data_contract")
            if isinstance(dc, dict):
                dc["storage_namespace"] = "deterministic/candidate-applied"
            return candidate

    port = _CandidatePort()

    class _Flaky(FakeDockerRunner):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def run(self, cmd, *, stdin, timeout_s):
            self.calls += 1
            if self.calls == 1:
                return CommandResult(stdout="{}", stderr="first fail", exit_code=1)
            return ch._command_result(ch._ok_sandbox_report())

    result = run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        bundle_service=ch._svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}), docker_runner=_Flaky(),
        user_id="u1", session_id="s", run_id="rework-candidate",
        rework_llm="on", rework_llm_port=port,
    )
    assert port.calls == 1
    assert result["branch"] == "compiled"
    dc = result["spec"].get("data_contract") if result["spec"] else None
    assert isinstance(dc, dict)
    assert dc["storage_namespace"] == "deterministic/candidate-applied"


def test_channel_rework_still_fails_goes_failed() -> None:
    """N2：返工后仍不过门禁 → failed + evidence_ref 非空（不假绿）。"""
    import tests.unit.test_v3_deterministic_channel as ch
    from services.deterministic.channel import run_deterministic
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    port = _fake_port_returns_same_spec()
    result = run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        bundle_service=ch._svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=CommandResult(stdout="{}", stderr="", exit_code=1)),
        user_id="u1", session_id="s", run_id="rework-still-fail",
        rework_llm="on", rework_llm_port=port,
    )
    assert port.calls == 1  # 有界
    assert result["branch"] == "failed"
    assert result["evidence_ref"]  # 真留证
