"""M03 显式 agent loop（§11.3 run() 逐字语义，【推导·需评审】辅助函数已固化）。

模型只在 CALL_MODEL 分支出现；每个 super-step：decide → 动作 → checkpoint.save
→ events.emit → budget.check → lease.heartbeat；finally 释放租约。

类型改造后：模型输出的可信结论写入 :class:`SpecState` 显式字段（不再用
``spec["__xxx"]`` 动态键）；所有函数参数、闭包默认值均有明确类型。
"""
from __future__ import annotations

import json
import logging
from typing import cast
from uuid import uuid4

from packages.agent_core.budget import meter
from packages.agent_core.constants import LEASE_TTL_S, MAX_ITER
from packages.agent_core.context.slots import assemble
from packages.agent_core.errors import CoreError
from packages.agent_core.events import seq as events_seq
from packages.agent_core.injection import Deps, Lease
from packages.agent_core.loop.decide import decide
from packages.agent_core.state.checkpoint import CheckpointStoreExtras, save_checkpoint
from packages.agent_core.tools.invoke import load as load_capability
from packages.agent_core.types import (
    CheckpointNode,
    Decision,
    DecisionKind,
    Input,
    JsonObject,
    JsonValue,
    Message,
    ModelOutput,
    SlotConfig,
    SpecState,
    State,
    TerminalKind,
    Usage,
)
from packages.contracts.enums import AgentEventKind, ReasonCode, SessionState

logger = logging.getLogger(__name__)
"""审计旁路日志：留证失败时告警，不打挂业务（M2 留证隔离纪律）。"""

# 模型 final JSON 中会被认定为「可信结论」并写入 SpecState 的字段白名单。
# 键为 JSON 字段名，值为 SpecState 上的目标属性名；白名单之外一律忽略。
_TRUSTED_SIGNAL_KEYS: tuple[str, ...] = (
    "signal", "reason", "bundle_ref", "evidence", "question", "capability_id", "level", "args", "surface", "tier",
)


def _persist(state: State, deps: Deps, node: CheckpointNode) -> State:
    """同步落盘（rev 单调 + prev_hash 链），返回 rev 已递增的新 State。

    rev 取 max(state.rev, latest_rev+1)：恢复时即使 checkpoint 的 rev 领先于
    loaded state 也不会撞车（主键 (run_id, rev) 唯一）。
    """
    store = deps.checkpoint_store
    rev = max(state.rev, store.latest_rev(state.run_id) + 1)
    to_save = state.bump(rev=rev)
    save_checkpoint(store, to_save, node)
    # states 是内存替身便利属性，cast 到带 extras 的视图后写入（规则 6 边界收口）
    cast(CheckpointStoreExtras, store).states[state.run_id] = to_save
    return to_save.bump(rev=rev + 1)


def apply_model_output(state: State, out: ModelOutput) -> State:
    """把「唯一不可信变量」转成可断言的状态迁移。

    final JSON 严格解析；白名单内的字段写入 SpecState 的显式槽位作为可信结论；
    usage 计入本 super-step（由 run 在调用处 add）。
    半截 JSON 不在此触发副作用——本内核只解析 final（partial 归 structured）。
    """
    payload: JsonValue = json.loads(out.content) if out.content else {}
    trusted: JsonObject = {}
    if isinstance(payload, dict):
        # 只拷贝白名单字段，杜绝模型输出污染整个 spec（含 extras）的动态键注入
        for key in _TRUSTED_SIGNAL_KEYS:
            # 逐白名单键读取：命中才写入，避免用 None 覆盖已有可信槽位
            if key in payload:
                trusted[key] = payload[key]
    # 合并现有槽位与本次可信结论后整体校验（model_validate 会校验全部字段），
    # 模型输出的非法 tier / args 会在写入点被 Pydantic 拒绝（fail fast）。
    merged = {**state.spec.model_dump(), **trusted}
    spec = SpecState.model_validate(merged)
    return state.bump(spec=spec)


def apply_capability_result(
    state: State,
    result_ref: str | None,
    *,
    denied_reason: str | None = None,
    count: int | None = None,
    tokens: int | None = None,
) -> State:
    """能力结果引用回灌：写入 tool_result_ref 并把信号切回 CALL_MODEL。

    ADR-003 / B3：拒绝时带 reason 让下一轮 decide/模型自纠正（不抛异常卡死）。
    """
    update: dict[str, JsonValue] = {
        "tool_result_ref": result_ref,
        "signal": DecisionKind.CALL_MODEL.value,
    }
    if denied_reason is not None:
        update["reason"] = denied_reason
    if count is not None:
        update["capability_count"] = count
    if tokens is not None:
        update["capability_tokens"] = tokens
    spec = state.spec.model_copy(update=update)
    return state.bump(spec=spec)


def _append_denial_message(state: State, capability_id: str, reason: str | None) -> State:
    """把能力拒绝事实注入 system 消息，让下一轮模型自纠正（B3）。"""
    msg = Message(
        "system",
        f"能力加载被拒：{capability_id}（{reason}）。请改用其它能力或修正参数后重试。",
    )
    msgs = [*state.spec.messages, msg]
    return state.bump(spec=state.spec.model_copy(update={"messages": msgs}))


def _store_capability_result(deps: Deps, run_id: str, rev: int, output: str) -> str:
    """能力结果落对象存储，ref 只存纯 key（ADR-003 / B5：不内联内容进 checkpoint）。"""
    if deps.object_store is None:
        # 内核兜底：无对象存储时用纯 key 形态（内容由调用方另取，生产必注入 store）
        return f"capability-result/{run_id}/{rev}"
    return deps.object_store.put(f"capability-result/{run_id}/{rev}", output.encode("utf-8"))


def _record_capability_audit(
    deps: Deps,
    session_id: str,
    run_id: str,
    trace_id: str,
    capability_id: str,
    level: int,
    surface: str,
    decision: str,
    started_ms: int,
) -> None:
    """记录一条能力请求审计（ADR-003 未完成项3）；无审计端口时跳过。"""
    if deps.capability_audit is None:
        return
    from time import monotonic

    try:
        latency_ms = int((monotonic() * 1000) - started_ms)
        deps.capability_audit.record(
            session_id, run_id, trace_id, capability_id, level, surface, decision, latency_ms
        )
    except Exception as exc:  # noqa: BLE001 - 审计是旁路留证，失败不得击穿校验链
        logger.warning("capability audit record failed (evidence dropped): %s", exc)


def _default_capability_guard(
    capability_id: str, level: int, surface: str, count: int, tokens: int
) -> tuple[bool, str | None]:
    """内核默认闸门（ADR-003 / B2）：白名单/surface/level 三项确定性判定。

    生产由 Deps.capability_guard 注入完整五项（含 count/token 上限）；此处为
    内核兜底，不 import 上层 services，避免分层反转。
    """
    from packages.agent_core.tools.registry import REGISTRY

    if capability_id not in REGISTRY:
        return False, "CAPABILITY_DENIED: unregistered"
    if surface != "generation":
        return False, "CAPABILITY_DENIED: surface"
    if level not in (1, 2):
        return False, "INPUT_INVALID: level"
    return True, None


def _acquire(deps: Deps, run_id: str) -> Lease | None:
    """抢对象式租约；未注入 LeaseProvider 时返回 None（单测/简化运行）。"""
    if deps.lease_provider is None:
        return None
    return deps.lease_provider.acquire(run_id, deps.owner, LEASE_TTL_S)


def run(session_id: str, run_id: str, user_input: Input | str, deps: Deps) -> State:
    """§11.3 原文签名（追加 deps 注入参数；user_input 可为 Input 或 str）。"""
    # 入参归一化：结构化 Input 拆出 text/grid，裸字符串按纯文本、无网格处理
    catalog: tuple[JsonObject, ...] = ()
    if isinstance(user_input, Input):
        text, grid = user_input.text, user_input.grid
        catalog = user_input.capability_catalog
    else:
        text, grid = user_input, None

    # 1. 装载状态（checkpoint.load 优先）
    state = deps.checkpoint_store.load(run_id)
    if state is None:
        state = State.initial(session_id, run_id, text, grid)

    # ADR-003 / B1：把 L0 能力目录装配进上下文（系统契约后的固定槽位），
    # 让模型看得见能力并经 LOAD_CAPABILITY 自主选择。用 assemble 保证固定槽位+caching。
    if catalog:
        catalog_messages = assemble(state, SlotConfig(capability_catalog=catalog))
        state = state.bump(spec=state.spec.model_copy(update={"messages": catalog_messages}))

    # 2. 抢租约
    lease = _acquire(deps, run_id)
    trace_id = f"trace-{uuid4().hex[:12]}"

    def send(
        seq: int,
        state: State,
        decision: Decision,
        kind: AgentEventKind | str,
        payload: JsonValue = None,
    ) -> int:
        """发一条事件并返回递增后的 seq（kind 取 AgentEventKind 契约值，显式传参避开闭包推断陷阱）。"""
        events_seq.emit(
            seq, state.phase, decision, bus=deps.event_bus,
            session_id=session_id, run_id=run_id, trace_id=trace_id,
            payload=payload, now=deps.clock.now(), kind=kind,
        )
        return seq + 1

    # 装载后先做一次预算检查（恢复点也受硬上限约束）
    meter.check(state)

    try:
        # 挂起态（AWAITING_INPUT）不占计算资源：退出循环，等待 resume
        while (not state.terminal
               and state.phase != SessionState.AWAITING_INPUT
               and state.iteration < MAX_ITER):
            # 4. 纯函数决策
            decision: Decision = decide(state)
            seq_no = events_seq.next_seq(deps.event_bus.all(), session_id)

            # 5. 分支动作：按 Decision.kind 分派，与 decide 的出口一一对应
            if decision.kind == DecisionKind.CALL_MODEL:
                out = deps.model.complete(decision.messages, tier=decision.tier, stream=True)
                # 网关可能返回非 ModelOutput（如单测桩/异常兜底字符串）：统一包装再计入 usage
                if not isinstance(out, ModelOutput):
                    out = ModelOutput(content=str(out), usage=Usage())
                state = apply_model_output(state, out)
                state = meter.add(state, usage=out.usage, tier=out.tier)
            elif decision.kind == DecisionKind.LOAD_CAPABILITY:
                # ADR-003：① 过闸门（B2）② 执行/加载（FR-4）③ 失败转自纠正（B3）
                # ④ L1/L2 均计 token（B4）⑤ 结果落对象存储、ref 不内联（B5）
                # ⑥ 每个请求留审计证据（未完成项3）。
                from time import monotonic

                started_ms = int(monotonic() * 1000)
                guard_check = (
                    deps.capability_guard.check
                    if deps.capability_guard is not None
                    else _default_capability_guard
                )
                allowed, deny_reason = guard_check(
                    decision.capability_id or "",
                    decision.level,
                    decision.surface,
                    state.spec.capability_count,
                    state.spec.capability_tokens,
                )
                if not allowed:
                    state = apply_capability_result(
                        state,
                        f"capability-denied/{decision.capability_id}",
                        denied_reason=deny_reason,
                    )
                    # B3：把拒绝事实注入消息，让下一轮模型自纠正（而不是盲目重试）
                    state = _append_denial_message(state, decision.capability_id or "", deny_reason)
                    state = meter.add(state, tool_calls=1)
                    _record_capability_audit(
                        deps, session_id, run_id, trace_id, decision.capability_id or "",
                        decision.level, decision.surface, "DENIED", started_ms,
                    )
                    continue
                try:
                    result = load_capability(decision)
                except CoreError as exc:
                    # 业务失败（未注册/参数/授权）转自纠正；infra 上抛交重试策略
                    if exc.retryable is False:
                        state = apply_capability_result(
                            state, f"capability-denied/{decision.capability_id}", denied_reason=exc.code
                        )
                        state = _append_denial_message(state, decision.capability_id or "", exc.code)
                        state = meter.add(state, tool_calls=1)
                        _record_capability_audit(
                            deps, session_id, run_id, trace_id, decision.capability_id or "",
                            decision.level, decision.surface, exc.code, started_ms,
                        )
                        continue
                    raise
                ref = _store_capability_result(deps, run_id, state.rev, result.output)
                state = apply_capability_result(
                    state,
                    ref,
                    count=state.spec.capability_count + 1,
                    tokens=state.spec.capability_tokens + len(result.output),
                )
                state = meter.add(
                    state,
                    tool_calls=1,
                    usage=Usage(input_tokens=0, output_tokens=len(result.output)),
                )
                _record_capability_audit(
                    deps, session_id, run_id, trace_id, decision.capability_id or "",
                    decision.level, decision.surface, "LOADED", started_ms,
                )
            elif decision.kind == DecisionKind.REPAIR:
                # 回边，不新增 Run；计数与信号切换由 repair.feedback 统一负责
                from packages.agent_core.structured.repair import (
                    feedback as repair_feedback,
                )
                state = repair_feedback(state, decision.errors)
            elif decision.kind == DecisionKind.ASK_USER:
                # 记录本轮澄清次数后暂停，等待用户输入再经 decide 的 RESUME 分支续跑
                state = state.bump(clarify_count=state.clarify_count + 1).pause()
            elif decision.kind == DecisionKind.RESUME_FROM_CLARIFICATION:
                # 澄清已回填：恢复 phase 到运行态，下一轮 super-step 继续
                state = state.resume()
            elif decision.kind == DecisionKind.AWAIT_INPUT:
                # 业务挂起：pause 写入挂起原因，退出 while 等待外部恢复
                state = state.pause(reason=decision.reason_code)
            elif decision.kind in (DecisionKind.DONE, DecisionKind.HANDOFF, DecisionKind.FAILED):
                # 三种终态统一落终止快照；bundle_ref/evidence 取自可信槽位供回溯
                now = deps.clock.now()
                state = state.terminate(
                    decision,
                    now=now,
                    bundle_ref=state.spec.bundle_ref,
                    evidence_ref=state.spec.evidence,
                )

            # 6. 每 super-step 同步落盘（rev 单调 + prev_hash 链）
            state = state.bump(seq=seq_no, iteration=state.iteration + 1)
            state = _persist(state, deps, CheckpointNode.from_decision(decision.kind))

            # 7. 发事件（多种 kind：checkpoint / cost / 业务 kind / 终态）
            # 每个事件独立递增 seq（I8：连续、无重复、无倒序）
            cur_seq = seq_no
            # 捕获当前迭代快照传给 send：保住 load() 后的 None 收窄（闭包内会丢失）
            snap: State = state

            cur_seq = send(cur_seq, snap, decision, AgentEventKind.CHECKPOINT,
                           cast(JsonObject, {"rev": state.rev, "node": decision.kind}))
            cur_seq = send(cur_seq, snap, decision, AgentEventKind.COST,
                           cast(JsonObject, {"budget_used": dict(state.budget_used)}))
            if state.terminal is not None:
                # 终态事件按 kind 区分 done/error：前端据此关闭或展示失败态
                cur_seq = send(cur_seq, snap, decision,
                               AgentEventKind.DONE if state.terminal.kind == TerminalKind.DONE
                               else AgentEventKind.ERROR,
                               cast(JsonObject, {"terminal": state.snapshot()["terminal"]}))
            elif decision.kind == DecisionKind.ASK_USER:
                # 澄清提问事件：把问题文本推给前端 HITL 交互
                cur_seq = send(cur_seq, snap, decision, AgentEventKind.ASK_USER,
                               cast(JsonObject, {"question": state.spec.question}))
            elif decision.kind == DecisionKind.AWAIT_INPUT:
                # 挂起事件：透出 reason_code 供前端展示中断原因
                cur_seq = send(cur_seq, snap, decision, AgentEventKind.INTERRUPTED,
                               cast(JsonObject, {"reason_code": decision.reason_code}))
            state = state.bump(seq=cur_seq - 1)

            # 8. 成本检查（四维硬上限，确定性中断）
            meter.check(state)

            # 9. 续租
            if lease is not None:
                # 两种注入形态都要续：lease 是本次抢到的对象，provider 是模块级入口
                lease.heartbeat()
                if deps.lease_provider is not None:
                    deps.lease_provider.heartbeat(run_id, deps.owner)

        # 10. 收尾：仅当循环因 MAX_ITER 退出且无终态时补写终态快照
        # （正常终态已在 super-step 落盘，不重复写）
        if state.iteration >= MAX_ITER and state.terminal is None:
            state = state.terminate(
                Decision(DecisionKind.FAILED, reason_code=ReasonCode.RETRY_EXHAUSTED),
                now=deps.clock.now(),
                evidence_ref=f"max-iter/{run_id}",
            )
            state = _persist(state, deps, CheckpointNode.FINAL)
        return state
    finally:
        # 11. 释放租约（异常路径同样释放，防止租约泄漏）
        if lease is not None:
            lease.release()
            if deps.lease_provider is not None:
                deps.lease_provider.release(run_id, deps.owner)
