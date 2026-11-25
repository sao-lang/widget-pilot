"""v3 确定性通道 RED 测试（ADR-013 §9 最短闭环）。

一条真实纵向链路：描述+附件 → 解释器四分支 → 成分→WidgetSpec→TSX
→ 真三门禁（桩执行器）→ 真成包 → bundle_ref。全程零云端 LLM。

本文件先写、先跑 RED（模块/函数不存在），再实现转 GREEN。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from services.sandbox_runner.ports import CommandResult
from tests.support.m08 import (
    FakeDockerRunner,
    FakeSourceStore,
    MemoryEvidenceStore,
    sandbox_report,
)


def _command_result(value):
    return CommandResult(stdout=json.dumps(value), stderr="", exit_code=0)


def _ok_sandbox_report(capability: str = "", grid: str = "4x2"):
    """桥事件与 render 尺寸如实反映确定性产物（静态数据无 bridge 调用）。"""
    from services.sandbox_runner.render import grid_px

    width, height = grid_px(grid)
    events = (
        [{"type": "capability_request", "capability": capability, "allowed": True}]
        if capability else []
    )
    return sandbox_report(
        bridge_events=events,
        render={"grid": grid, "width_px": width, "height_px": height,
                "max_child_width_px": width - 20, "max_child_height_px": height - 10,
                "errors": []},
    )


@dataclass(frozen=True)
class _Att:
    id: str
    kind: str = "image"
    source_ref: str = ""


def _compile_module():
    import importlib
    return importlib.import_module("services.deterministic.compiler")


def _channel_module():
    import importlib
    return importlib.import_module("services.deterministic.channel")


# ---------- 1. 确定性编译器：成分 → WidgetSpec → TSX ----------

def test_slotfill_compiles_to_valid_widget_spec() -> None:
    """SlotFill(grid/data/host) → WidgetSpec 16 必填字段，过真 JSON Schema。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(
        intent_id="widget.generic", instance_id="h:0",
        slots={"grid": "4x2", "data": "待办", "host": "桌面"},
    )
    spec = compiler.compile_spec(fill, recipe_id="recipe.task.list.v2")
    # 16 必填字段
    for key in (
        "spec_version", "widget_id", "grid", "widget_type", "intent", "attachments",
        "layout_slots", "capabilities", "data_contract", "style", "constraints",
        "language", "react_version", "import_allowlist", "export_contract", "props_contract",
    ):
        assert key in spec, f"缺 WidgetSpec 字段 {key}"
    # 去类型化：widget_type 恒 custom（ADR-004）
    assert spec["widget_type"] == "custom"
    assert spec["grid"] == "4x2"
    assert spec["language"] == "tsx"
    assert spec["import_allowlist"] == ["react", "@widget/bridge"]
    # widget_id = uuid5(spec_hash)，可复现
    assert isinstance(spec["widget_id"], str) and len(spec["widget_id"]) >= 8
    # 真 schema 校验
    compiler.assert_valid_spec(spec)


def test_compiler_is_deterministic() -> None:
    """同成分两次编译逐字段一致（widget_id 不随机）。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(intent_id="widget.generic", instance_id="h:0",
                    slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    a = compiler.compile_spec(fill, recipe_id="recipe.task.list.v2")
    b = compiler.compile_spec(fill, recipe_id="recipe.task.list.v2")
    assert a == b


def test_spec_renders_to_whitelist_tsx() -> None:
    """WidgetSpec → TSX：只 import react/@widget/bridge、default Widget、能力声明一致。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(intent_id="widget.generic", instance_id="h:0",
                    slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    spec = compiler.compile_spec(fill, recipe_id="recipe.task.list.v2")
    tsx = compiler.render_tsx(spec)
    assert "export default" in tsx and "Widget" in tsx
    assert "dangerouslySetInnerHTML" not in tsx
    # 真静态扫描：import 白名单 + default widget + 能力调用已声明
    compiler.assert_tsx_contract_ok(spec, tsx)


def test_widget_id_reproducible_from_spec_hash() -> None:
    """uuid5(namespace, spec_hash)：改成分则 id 变，同成分则 id 定。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    f1 = SlotFill(intent_id="widget.generic", instance_id="h:0",
                  slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    f2 = SlotFill(intent_id="widget.generic", instance_id="h:0",
                  slots={"grid": "4x4", "data": "待办", "host": "桌面"})
    s1 = compiler.compile_spec(f1, recipe_id="recipe.task.list.v2")
    s2 = compiler.compile_spec(f2, recipe_id="recipe.task.list.v2")
    assert s1["widget_id"] != s2["widget_id"]


# ---------- 2. 四分支分流 ----------

def test_branch_compiled_to_bundle_end_to_end() -> None:
    """分支①：精确命中 → 成分→TSX→真三门禁→真成包，零 model.complete。"""
    channel = _channel_module()
    svc = _svc()
    evidence = MemoryEvidenceStore()
    result = channel.run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        attachments=(),
        bundle_service=svc,
        evidence_store=evidence,
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        source_key="code/sess/r1/widget.tsx",
        user_id="u1", session_id="sess", run_id="r1",
    )
    assert result["branch"] == "compiled"
    assert result["bundle"] is not None
    assert result["bundle"]["state"] == "GENERATED"
    assert isinstance(result["bundle"]["bundle_id"], str)
    # 全程零云端模型
    assert result["model_calls"] == 0
    assert result["generation_path"] == "deterministic"


def test_branch_clarification_on_missing_grid() -> None:
    """分支③：缺 grid → AWAITING_INPUT 追问，不调模型、不成包。"""
    channel = _channel_module()
    result = channel.run_deterministic(
        "桌面上加个卡片显示待办",
        attachments=(),
        bundle_service=_svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        user_id="u1", session_id="sess", run_id="r1",
    )
    assert result["branch"] == "clarification"
    assert "grid" in result["missing_slots"]
    assert result["bundle"] is None
    assert result["model_calls"] == 0


def test_branch_rejected_out_of_scope() -> None:
    """分支②：越界/拒绝 → FAILED，零模型、零成包。"""
    channel = _channel_module()
    result = channel.run_deterministic(
        "今天天气怎么样",
        attachments=(),
        bundle_service=_svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        user_id="u1", session_id="sess", run_id="r1",
    )
    assert result["branch"] == "rejected"
    assert result["bundle"] is None
    assert result["model_calls"] == 0


def test_branch_llm_fallback_when_interpreter_cannot_resolve() -> None:
    """分支④：解释器无法解析但 LLM 可做 → 标注 cloud-fallback，交 run()。"""
    channel = _channel_module()
    result = channel.run_deterministic(
        "随便做个好看的东西",
        attachments=(),
        bundle_service=_svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        user_id="u1", session_id="sess", run_id="r1",
    )
    assert result["branch"] == "llm_fallback"
    assert result["generation_path"] == "cloud-fallback"
    assert result["bundle"] is None


# ---------- 3. 附件用途驱动工具链 ----------

def test_attachment_derived_ref_feeds_spec() -> None:
    """附件工具链派生的 frame_at ref → 真实写进 WidgetSpec 的 layout_slots。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(
        intent_id="widget.generic", instance_id="h:0",
        slots={"grid": "4x2", "data": "待办", "host": "桌面",
               "derived_refs": {"frame_at": "derived/f3.500.webp#sha256:" + "b" * 64}},
    )
    spec = compiler.compile_spec(fill, recipe_id="recipe.task.list.v2")
    assert "frame_at" in json.dumps(spec, ensure_ascii=False)
    assert "derived/f3.500.webp" in spec["layout_slots"][0]["content"]
    compiler.assert_valid_spec(spec)


def test_attachment_purpose_unclear_goes_clarification() -> None:
    """附件用途无法从描述解析 → 反问用途，不成包、不调模型。"""
    from services.guardrails.interpreter.capability_planner import plan_attachment

    plan = plan_attachment("桌面上加个 4x2 卡片", (_Att("a1", "video", "video/s#sha256:" + "a" * 64),))
    assert plan.steps == ()  # 未提及附件用途 → 空计划，交上层澄清



# ---------- 4. 门禁失败返工 ----------

def test_gate_failure_then_llm_rework_bounded() -> None:
    """门禁两次不过 → 有界 LLM 返工一次；关开关时零 model.complete。"""
    channel = _channel_module()
    result = channel.run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        attachments=(),
        bundle_service=_svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=_always_fail_runner(),
        rework_llm="off",
        user_id="u1", session_id="sess", run_id="r1",
    )
    assert result["branch"] == "failed"
    assert result["bundle"] is None
    assert result["model_calls"] == 0  # rework_llm=off


def _always_fail_runner():
    from services.sandbox_runner.ports import CommandResult

    class _Fail:
        def run(self, command, *, stdin=b"", timeout_s=0):
            return CommandResult(stdout=json.dumps({"stages": {}, "interactions": [],
                                                     "logs": "forced failure", "snapshot": ""}),
                                 stderr="simulated failure", exit_code=1)

        def kill(self, container_name):
            return None

        def exists(self, container_name):
            return False

    return _Fail()


# ---------- helpers ----------

def _svc():
    from services.control_plane.bundles.service import BundleService
    from tests.support.bundles import (
        InMemoryBundleStore,
        RecordingArtifactAdoption,
        RecordingCorpusLink,
        RecordingEventSink,
        RecordingPreferenceRevocation,
    )
    from tests.support.control_plane import AuditLedger

    return BundleService(
        store=InMemoryBundleStore(), audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(), preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )


# ---------- P2 #36：修改意图定位 ----------

def test_modify_intent_locates_existing_bundle() -> None:
    """修改意图：从会话内 bundles 定位目标，target_bundle 落到 ChannelResult。"""
    from services.control_plane.bundles.service import BundleService
    from services.guardrails.interpreter.types import SlotFill
    from tests.support.bundles import (
        InMemoryBundleStore,
        RecordingArtifactAdoption,
        RecordingCorpusLink,
        RecordingEventSink,
        RecordingPreferenceRevocation,
    )
    from tests.support.control_plane import AuditLedger

    channel = _channel_module()
    store = InMemoryBundleStore()
    for i, bid in enumerate(("b-first", "b-existing"), start=1):
        store.insert({
            "bundle_id": bid, "user_id": "u1", "session_id": "sess",
            "state": "GENERATED", "created_at": f"2026-11-25T10:0{i}:00+00:00",
            "spec": {"intent": "桌面日历", "grid": "4x2"},
            "hash": "a" * 64,
        }, ())
    svc = BundleService(
        store=store, audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(), preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )

    class _Interp:
        def interpret(self, description, attachments=(), fields=None):
            from services.guardrails.interpreter.types import InterpretResult, LayerUsed
            fill = SlotFill(
                intent_id="WIDGET_MODIFY", instance_id="h:0",
                slots={"grid": "4x4", "data": "日历", "host": "桌面"},
            )
            return InterpretResult(kind="compiled", instances=(fill,),
                                   layer_used=LayerUsed.RULE, confidence=1.0)

    result = channel.run_deterministic(
        "把第二个日历改成 4x4", bundle_service=svc,
        evidence_store=MemoryEvidenceStore(), source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(
            result=_command_result(_ok_sandbox_report(grid="4x4"))),
        user_id="u1", session_id="sess", run_id="r1", interpreter=_Interp(),
    )
    # P2/P5 真重成包：复用目标 bundle_id + 追加版本，不新建无关包。
    assert result["branch"] == "compiled"
    assert result["target_bundle"] is not None
    assert result["bundle"]["bundle_id"] == "b-existing"  # 复用同一 id
    assert len(store.rows) == 2  # 没有凭空多出一个新包
    assert len(store.versions["b-existing"]) >= 1  # 版本链已落新节点
    assert result["generation_path"] == "deterministic"  # 零模型


def test_modify_intent_without_bundles_asks_target() -> None:
    """会话内无 bundle → clarification(target)，不硬造修改。"""
    from services.guardrails.interpreter.types import (
        InterpretResult,
        LayerUsed,
        SlotFill,
    )

    channel = _channel_module()

    class _Interp:
        def interpret(self, description, attachments=(), fields=None):
            fill = SlotFill(
                intent_id="WIDGET_MODIFY", instance_id="h:0",
                slots={"grid": "4x4", "data": "日历", "host": "桌面"},
            )
            return InterpretResult(kind="compiled", instances=(fill,),
                                   layer_used=LayerUsed.RULE, confidence=1.0)

    result = channel.run_deterministic(
        "把第二个日历改成 4x4", bundle_service=_svc(),
        evidence_store=MemoryEvidenceStore(), source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        user_id="u1", session_id="empty", run_id="r1", interpreter=_Interp(),
    )
    assert result["branch"] == "clarification"
    assert result["question_kind"] == "target"


# ---------- P0 附件生成侧：render_tsx 内联图片 ----------

def test_render_tsx_emits_image_node_safely() -> None:
    """有 image_data_url 时 render_tsx 输出 <img>，且恶意引号/花括号被安全转义。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(intent_id="widget.generic", instance_id="h:0",
                    slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    spec = compiler.compile_spec(fill)
    spec["layout_slots"][0]["image_data_url"] = "data:image/webp;base64,AAAA"
    tsx = compiler.render_tsx(spec)
    assert "<img" in tsx
    assert "data:image/webp;base64,AAAA" in tsx


def test_render_tsx_rejects_invalid_image_data_url() -> None:
    """非法 data URL（含脚本/非白名单 MIME）不注入 JSX，降级纯文本。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(intent_id="widget.generic", instance_id="h:0",
                    slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    spec = compiler.compile_spec(fill)
    spec["layout_slots"][0]["image_data_url"] = 'data:image/webp;base64,"><script>x</script>'
    tsx = compiler.render_tsx(spec)
    assert "<img" not in tsx
    assert "<script>" not in tsx


def test_render_tsx_without_image_keeps_text_only() -> None:
    """无图片时保持原纯文本节点（零回归）。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(intent_id="widget.generic", instance_id="h:0",
                    slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    tsx = compiler.render_tsx(compiler.compile_spec(fill))
    assert "<img" not in tsx
    assert "<span>" in tsx


# ---------- P0 N>1：多实例不静默丢弃 ----------

def test_multi_instances_produce_multiple_bundles() -> None:
    """两个宿主+两个 grid → 两个 bundle，不再只取 instances[0]。"""
    channel = _channel_module()
    svc = _svc()
    result = channel.run_deterministic(
        "主屏幕放一个 4x2 的任务清单，桌面放一个 4x2 的天气卡片",
        bundle_service=svc,
        evidence_store=MemoryEvidenceStore(), source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        user_id="u1", session_id="sess", run_id="r1-n1-multi",
    )
    assert result["branch"] == "compiled"
    assert len(result["bundles"]) == 2
    assert result["bundle"]["bundle_id"] == result["bundles"][0]["bundle_id"]
    assert result["bundles"][0]["bundle_id"] != result["bundles"][1]["bundle_id"]


def test_single_instance_still_one_bundle() -> None:
    """单实例保持原行为（零回归）。"""
    channel = _channel_module()
    result = channel.run_deterministic(
        "主屏幕放一个 4x2 的任务清单，显示待办",
        bundle_service=_svc(), evidence_store=MemoryEvidenceStore(),
        source_store=FakeSourceStore({}),
        docker_runner=FakeDockerRunner(result=_command_result(_ok_sandbox_report())),
        user_id="u1", session_id="sess", run_id="r1-n1-single",
    )
    assert len(result["bundles"]) == 1


def test_bundle_service_rework_appends_version_chain() -> None:
    """service.rework 复用 bundle_id、追加版本节点（parent 指向旧 head）。"""
    from services.control_plane.bundles.service import BundleService
    from services.control_plane.bundles.versions import append_version
    from services.deterministic.compiler import compile_spec
    from services.guardrails.interpreter.types import SlotFill
    from tests.support.bundles import (
        InMemoryBundleStore,
        RecordingArtifactAdoption,
        RecordingCorpusLink,
        RecordingEventSink,
        RecordingPreferenceRevocation,
    )
    from tests.support.control_plane import AuditLedger

    store = InMemoryBundleStore()
    old = {
        "bundle_id": "br1", "user_id": "u1", "session_id": "s1",
        "state": "GENERATED", "created_at": "2026-11-25T10:00:00+00:00",
        "spec": {"intent": "桌面日历", "grid": "2x2"},
        "source": {"content_ref": "old#sha256:" + "a" * 64},
        "compiled": {"content_ref": "old#sha256:" + "a" * 64, "hash": "a" * 64},
        "manifest": {"spec_hash": "a" * 64},
        "hash": "a" * 64,
    }
    v0 = append_version((), "old#sha256:" + "a" * 64, "a" * 64, "a" * 64,
                        bundle_id="br1", version_id="v0")
    store.insert(old, v0)
    svc = BundleService(
        store=store, audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(), preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )
    spec = compile_spec(SlotFill(intent_id="widget.generic", instance_id="h:0",
                                 slots={"grid": "4x4", "data": "日历", "host": "桌面"}))
    from types import SimpleNamespace as NS
    validation = NS(
        passed=True, reason_code=None, evidence_ref="ev#sha256:" + "b" * 64,
        stages=(), repair_count=0, handoff=False, elapsed_ms=100,
        output_guard=NS(passed=True, hit=False, violations=(), reason_code=None,
                        evidence_ref="ev#sha256:" + "b" * 64),
        contract_check=NS(passed=True, violations=(), spec_hash="b" * 64),
        sandbox_test=NS(
            passed=True, cases=(),
            evidence_refs=NS(
                compiled_ref="new#sha256:" + "b" * 64,
                compiled_hash="b" * 64,
                manifest_hash="b" * 64,
                log_ref="log#sha256:" + "b" * 64,
                snapshot_ref="snap#sha256:" + "b" * 64,
            ),
            evidence_ref="ev#sha256:" + "b" * 64,
        ),
    )
    result = svc.rework(
        base_bundle_id="br1", validation=cast(Any, validation), spec=spec,
        source_ref="new#sha256:" + "b" * 64, user_id="u1", session_id="s1", run_id="r1",
    )
    assert result is not None
    assert result["bundle_id"] == "br1"
    chain = store.versions["br1"]
    assert len(chain) == 2
    assert chain[1].parent_id == "v0"


# ---------- P6 多配方复用：feature → recipe_id ----------

def test_compile_spec_derives_recipe_from_feature() -> None:
    """不同 data 特征产出不同 recipe_version/storage_namespace（零模型）。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    def spec_for(data: str):
        return compiler.compile_spec(SlotFill(
            intent_id="widget.generic", instance_id="h:0",
            slots={"grid": "4x2", "data": data, "host": "桌面"},
        ))

    task = spec_for("待办")
    calendar = spec_for("日历")
    weather = spec_for("天气")
    assert task["data_contract"]["storage_namespace"] != calendar["data_contract"]["storage_namespace"]
    assert calendar["data_contract"]["storage_namespace"] != weather["data_contract"]["storage_namespace"]


def test_explicit_recipe_id_still_wins() -> None:
    """显式传 recipe_id 仍覆盖推导结果（向后兼容）。"""
    compiler = _compile_module()
    from services.guardrails.interpreter.types import SlotFill

    spec = compiler.compile_spec(
        SlotFill(intent_id="widget.generic", instance_id="h:0",
                 slots={"grid": "4x2", "data": "待办", "host": "桌面"}),
        recipe_id="recipe.custom.v9",
    )
    assert spec["data_contract"]["storage_namespace"] == "deterministic/recipe.custom.v9"


def test_rework_after_modify_keeps_llm_rework_label() -> None:
    """B1（终检复审）：修改意图+LLM返工成功，generation_path 不得重成包覆盖回 deterministic。"""
    from services.deterministic.channel import run_deterministic
    from services.guardrails.interpreter.types import (
        InterpretResult,
        LayerUsed,
        SlotFill,
    )
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    class _Interp:
        def interpret(self, description, attachments=(), fields=None):
            fill = SlotFill(
                intent_id="WIDGET_MODIFY", instance_id="h:0",
                slots={"grid": "4x4", "data": "日历", "host": "桌面"},
            )
            return InterpretResult(kind="compiled", instances=(fill,),
                                   layer_used=LayerUsed.RULE, confidence=1.0)

    class _Flaky(FakeDockerRunner):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def run(self, cmd, *, stdin, timeout_s):
            self.calls += 1
            if self.calls == 1:
                return CommandResult(stdout="{}", stderr="first fail", exit_code=1)
            return _command_result(_ok_sandbox_report(grid="4x4"))

    from services.control_plane.bundles.service import BundleService
    from tests.support.bundles import (
        InMemoryBundleStore,
        RecordingArtifactAdoption,
        RecordingCorpusLink,
        RecordingEventSink,
        RecordingPreferenceRevocation,
    )
    from tests.support.control_plane import AuditLedger

    store = InMemoryBundleStore()
    for bid in ("b-first", "b-target"):
        store.insert({
            "bundle_id": bid, "user_id": "u1", "session_id": "sess",
            "state": "GENERATED", "created_at": f"2026-11-25T10:0{1 if bid == 'b-first' else 2}:00+00:00",
            "spec": {"intent": "桌面日历", "grid": "2x2"},
            "source": {"content_ref": "old#sha256:" + "a" * 64},
            "compiled": {"content_ref": "old#sha256:" + "a" * 64, "hash": "a" * 64},
            "manifest": {"spec_hash": "a" * 64}, "hash": "a" * 64,
        }, ())
    svc = BundleService(
        store=store, audit=AuditLedger(), events=RecordingEventSink(),
        corpus=RecordingCorpusLink(), preference_revocation=RecordingPreferenceRevocation(),
        adoption=RecordingArtifactAdoption(), confirm_secret="test-confirm-secret",
    )

    class _Port:
        def __init__(self): self.calls = 0
        def rework(self, spec, failures, description):
            self.calls += 1
            return spec

    port = _Port()
    result = run_deterministic(
        "把第二个日历改成 4x4", bundle_service=svc,
        evidence_store=MemoryEvidenceStore(), source_store=FakeSourceStore({}),
        docker_runner=_Flaky(), user_id="u1", session_id="sess", run_id="r1-b1",
        interpreter=_Interp(), rework_llm="on", rework_llm_port=port,
    )
    assert port.calls == 1
    assert result["branch"] == "compiled"
    assert result["model_calls"] == 1
    assert result["generation_path"] == "llm-rework"  # 不再被重成包覆盖
    assert result["bundle"] is not None
    assert result["bundle"]["bundle_id"] == "b-target"
