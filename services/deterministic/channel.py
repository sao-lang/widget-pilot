"""确定性通道编排（ADR-013 §9 纵向闭环）：解释 → 编译 → 三门禁 → 成包。

四分支统一返回 dict（内部通道结果，不进公共 JSON Schema）：
- compiled      : 零云端模型编译 + 真门禁 + 真成包
- clarification : 缺槽位/目标不明 → 追问（question_kind + missing_slots）
- rejected      : 越界/拒绝/附件不安全 → FAILED，零模型
- llm_fallback  : 解释器解析不出 → 交 run()→CALL_MODEL
- failed        : 门禁二次不过且返工关闭/失败 → FAILED

依赖全部注入（evidence/source/docker/bundle），生产由 worker/composition root 提供；
本模块零云端模型调用，只有 llm_fallback 分支把决定权交回编排层。
"""
from __future__ import annotations

import logging
import re
from typing import TypedDict

from packages.agent_core.types import JsonObject
from services.deterministic.compiler import (
    assert_valid_spec,
    build_manifest,
    compile_spec,
    render_tsx,
)
from services.guardrails.interpreter.router import InterpreterRouter
from services.guardrails.interpreter.rule import RuleInterpreter
from services.guardrails.interpreter.types import InterpretResult

__all__ = ["ChannelResult", "run_deterministic"]

logger = logging.getLogger(__name__)


class ChannelResult(TypedDict):
    """确定性通道内部结果（不进公共 JSON Schema；字段集确定，禁 Any）。

    所有键恒由 run_deterministic 的 base 填充（缺失值以 None/空串占位），
    故为 required 键——访问方无需 KeyError 防护。
    """

    branch: str
    bundle: JsonObject | None
    spec: JsonObject | None
    missing_slots: list[str]
    question_kind: str
    model_calls: int
    generation_path: str
    reason: str
    evidence_ref: str
    target_bundle: JsonObject | None
    target_choices: list[JsonObject] | None
    bundles: list[JsonObject]
    elapsed_ms: int
    session_items: list[JsonObject] | None


def _branch_from(result: InterpretResult) -> str:
    """把解释器 kind 细分到四分支。"""
    if result.kind == "compiled":
        return "compiled"
    if result.kind == "fallback":
        return "llm_fallback"
    # clarification 三分：越界证据 → rejected；缺 intent 且无越界 → llm_fallback；其余 → 追问
    if any(str(e).startswith("out_of_scope") for e in result.evidence):
        return "rejected"
    if "intent" in result.missing_slots:
        return "llm_fallback"
    return "clarification"


# v2fix 第5组：纯查询意图识别（零模型）。只识别明确的"列出/查一下"句式，
# 且不含生成动词，避免把"查完再做个widget"误判为纯查询。
_SESSION_QUERY_PATTERN = re.compile(
    r"(?:查一下|查查|列出|看看|给我看|显示一下).{0,20}(?:widget|控件|组件|小组件)"
    r"|(?:哪些|已有|生成过).{0,8}(?:widget|控件|组件|小组件)"
    r"|(?:widget|控件).{0,8}(?:有哪些|生成了哪些|有哪些)"
)


def _is_session_query(description: str) -> bool:
    from services.guardrails.constants import WIDGET_GENERATION_PATTERNS
    if not _SESSION_QUERY_PATTERN.search(description):
        return False
    # 查询句里的「生成了哪些widget」会被生成动词正则命中，属明确查询语境，
    # 先排除这类"生成了哪些/生成过"问法再判生成动词。
    query_only = re.sub(r"生成了哪些|生成过哪些|有哪些", "", description)
    return not any(re.search(p, query_only, re.IGNORECASE) for p in WIDGET_GENERATION_PATTERNS)


def run_deterministic(
    description: str,
    *,
    attachments: tuple = (),
    bundle_service,
    evidence_store,
    source_store,
    docker_runner,
    source_key: str = "code/deterministic/widget.tsx",
    user_id: str = "u",
    session_id: str = "s",
    run_id: str = "r",
    load_fn=None,
    rework_llm: str = "off",
    rework_llm_port=None,
    interpreter=None,
    session_query=None,
) -> ChannelResult:
    """一条描述 → 一个通道结果 dict；不调云端模型（llm_fallback 只标注）。"""
    itp = interpreter or InterpreterRouter(rule=RuleInterpreter(
        capability_load_fn=load_fn if load_fn is not None else None,
    ))
    result = itp.interpret(description, attachments, {})
    branch = _branch_from(result)

    # rejected 必须带非空 reason_code（Terminal 不变量：FAILED 需 reason+evidence）。
    reason = str(result.reason or "") or (
        "INTENT_OUT_OF_SCOPE" if branch == "rejected" else ""
    )
    base: ChannelResult = {
        "branch": branch,
        "bundle": None,
        "spec": None,
        "missing_slots": list(result.missing_slots),
        "question_kind": result.question_kind,
        "model_calls": 0,
        "generation_path": "deterministic",
        "reason": reason,
        "evidence_ref": "",
        "target_bundle": None,
        "target_choices": None,
        "bundles": [],
        "elapsed_ms": 0,
        "session_items": None,
    }
    # v2fix Round3 B2：纯查询检测前置到所有分支之前——带 grid 的查询句
    # （「查一下本会话生成了哪些widget，比如2x2的」）解释器会 compiled，
    # 若只认 clarification 分支就会误编译成新包。
    if _is_session_query(description):
        items = session_query.list_session_widgets(user_id, session_id, 50) if session_query else []
        base.update(branch="clarification", question_kind="session_query",
                    session_items=list(items))
        return base

    if branch != "compiled":
        if branch == "llm_fallback":
            base["generation_path"] = "cloud-fallback"
        return base

    # v2fix 第5组：真取消——不编译、不重成包，直接返回取消结果。
    # 原实现把 CANCEL 与 MODIFY 同进 resolve_target→rework，语义完全错。
    fill = result.instances[0]
    if fill.intent_id == "WIDGET_CANCEL":
        base.update(branch="clarification", question_kind="cancel",
                    missing_slots=["cancel_target"])
        return base

    # P2 #36：修改意图先定位会话内目标 bundle（不定位就编译等于改错对象）。
    rework_base_id = ""
    if fill.intent_id in ("WIDGET_MODIFY", "WIDGET_CONTINUE", "WIDGET_CANCEL"):
        from services.control_plane.bundles.registry import resolve_target
        from services.control_plane.bundles.target_hint import parse_target_hint

        bundles = bundle_service.list_by_session(user_id, session_id, None, 50)
        if not bundles:
            base.update(branch="clarification", question_kind="target",
                        missing_slots=["target_bundle"])
            return base
        hint = parse_target_hint(description)
        method = str(hint.pop("method", "last"))
        target = resolve_target(bundles, method, **hint)  # type: ignore[arg-type]
        if isinstance(target, dict) and "choices" in target:
            base.update(branch="clarification", question_kind="target",
                        missing_slots=["target_bundle"],
                        target_choices=target["choices"])  # type: ignore[assignment]
            return base
        if isinstance(target, dict):
            base["target_bundle"] = target
            # P2/P5 重成包（纯软件，零模型）：用户新槽位优先，未说的从目标
            # spec 继承；后续 compile_one 走 bundle_service.rework 追加版本，
            # 不再新建无关 bundle（B1 假绿的根因已消除）。
            from dataclasses import replace as _dc_replace

            from services.deterministic.rework import merge_rework_slots

            target_spec = target.get("spec")
            if not isinstance(target_spec, dict):
                base.update(branch="clarification", question_kind="target",
                            missing_slots=["target_bundle"])
                return base
            merged = merge_rework_slots(target_spec, fill)
            result = _dc_replace(result, instances=(merged,))  # type: ignore[arg-type]
            rework_base_id = str(target.get("bundle_id", ""))

    # 成分 → WidgetSpec → TSX → 三门禁 → 成包。N>1 逐个实例走完整闭环，
    # 任一失败整体 failed（保守：不留半个成功让上游误判）。
    from services.control_plane.bundles.adapter import is_validation_passed
    from services.deterministic.compiler import (
        assert_tsx_contract_ok,
        embed_assets,
    )
    from services.guardrails.output_ports import make_object_ref
    from services.sandbox_runner.entry import validate_and_test

    def gate(one_spec: JsonObject, key: str):
        """spec → source_ref → 三门禁；返回 (validation, ref, source_bytes)。"""
        one_tsx = render_tsx(one_spec)
        assert_tsx_contract_ok(one_spec, one_tsx)
        payload = one_tsx.encode("utf-8")
        one_ref = make_object_ref(key, payload)
        # key 不同则 ref 不同，避开 validate_and_test 全局结果缓存命中旧失败。
        if hasattr(source_store, "sources") and isinstance(source_store.sources, dict):
            source_store.sources[one_ref] = payload
        elif hasattr(source_store, "put"):
            source_store.put(key, payload)
        validation = validate_and_test(
            one_spec, one_ref, build_manifest(one_spec), 0,
            session_id=session_id, run_id=run_id,
            evidence_store=evidence_store, source_store=source_store,
            docker_runner=docker_runner,
            prefix=f"evidence/{session_id}/{run_id}",
        )
        return validation, one_ref

    def compile_one(single_fill, key: str) -> tuple[JsonObject, JsonObject, int] | None:
        """单实例：spec → 三门禁；失败最多 LLM 返工一次（同款门禁不豁免）。"""
        from services.deterministic.rework import try_bound_rework

        one_spec = compile_spec(single_fill)
        embed_assets(one_spec, single_fill.slots.get("derived_refs"), source_store)
        assert_valid_spec(one_spec)
        validation, one_ref = gate(one_spec, key)

        if not is_validation_passed(validation):
            # 返工③：最多一次有界 LLM 返工，输出仍过同款三门禁（不豁免）。
            candidate = try_bound_rework(
                one_spec, validation, description, rework_llm_port,
                rework_llm=rework_llm,
            )
            if candidate is not None:
                rework_key = key.rsplit(".", 1)[0] + ".rework.tsx"
                revalidated, reref = gate(candidate, rework_key)
                if is_validation_passed(revalidated):
                    one_spec, one_ref, validation = candidate, reref, revalidated
                    base["generation_path"] = "llm-rework"
                    base["model_calls"] = 1  # 真实端口仅调一次（有界）
                    logger.info("有界返工成功（%s→llm-rework）", base["reason"])
                else:
                    validation = revalidated  # 返工后仍失败 → 用第二份证据落 failed
            if not is_validation_passed(validation):
                base["reason"] = (
                    validation.reason_code.value if validation.reason_code else "GATE_FAILED"
                )
                base["evidence_ref"] = validation.evidence_ref
                logger.info("确定性产物三门禁未过（%s），rework_llm=%s",
                            base["reason"], rework_llm)
                return None
        if rework_base_id:
            bundle = bundle_service.rework(
                base_bundle_id=rework_base_id, validation=validation,
                spec=one_spec, source_ref=one_ref,
                user_id=user_id, session_id=session_id, run_id=run_id,
            )
            if bundle is None:  # 目标消失/越权：fail-closed，不新建无关包
                base["reason"] = "TARGET_UNAVAILABLE"
                return None
            # 不覆盖 generation_path：重成包本身零模型，但若本轮已走过 LLM 返工，
            # 标注须保持 llm-rework（与 model_calls=1 一致，成本账三口径不串）。
        else:
            bundle = bundle_service.assemble(
                validation=validation, spec=one_spec, source_ref=one_ref,
                user_id=user_id, session_id=session_id, run_id=run_id,
            )
        return one_spec, bundle, validation.elapsed_ms

    built: list[JsonObject] = []
    first_spec: JsonObject | None = None
    total_ms = 0
    for index, single_fill in enumerate(result.instances):
        outcome = compile_one(
            single_fill,
            f"{source_key}" if index == 0 else f"{source_key.rsplit('/', 1)[0]}/w{index}.tsx",
        )
        if outcome is None:
            base["branch"] = "failed"
            return base
        one_spec, bundle, elapsed_ms = outcome
        if first_spec is None:
            first_spec = one_spec
        built.append(bundle)
        total_ms += elapsed_ms

    if not built:  # 防御：compiled 分支必有实例，否则不假绿
        base["branch"] = "failed"
        base["reason"] = "NO_BUNDLE"
        return base
    base["bundles"] = built
    base["elapsed_ms"] = total_ms  # 沙箱真耗时回灌成本账（ADR-013 §3.4）
    base["spec"] = first_spec
    base["bundle"] = built[0]  # 兼容 dispatcher 单值消费（session.bundle_ref）
    return base
