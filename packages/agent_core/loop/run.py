"""M03 显式 agent loop（§11.3 run() 逐字语义，【推导·需评审】辅助函数已固化）。

模型只在 CALL_MODEL 分支出现；每个 super-step：decide → 动作 → checkpoint.save
→ events.emit → budget.check → lease.heartbeat；finally 释放租约。
"""
from __future__ import annotations

import json
from uuid import uuid4

from packages.agent_core.budget import meter
from packages.agent_core.constants import LEASE_TTL_S, MAX_ITER
from packages.agent_core.events import seq as events_seq
from packages.agent_core.injection import Deps, Lease
from packages.agent_core.loop.decide import decide
from packages.agent_core.state.checkpoint import save_checkpoint
from packages.agent_core.types import Decision, ModelOutput, State, Usage
from packages.contracts.enums import SessionState


def _persist(state: State, deps: Deps, node: str) -> State:
    """同步落盘（rev 单调 + prev_hash 链），返回 rev 已递增的新 State。

    rev 取 max(state.rev, latest_rev+1)：恢复时即使 checkpoint 的 rev 领先于
    loaded state 也不会撞车（主键 (run_id, rev) 唯一）。
    """
    store = deps.checkpoint_store
    rev = max(state.rev, store.latest_rev(state.run_id) + 1)
    to_save = state.bump(rev=rev)
    save_checkpoint(store, to_save, node)
    store.states[state.run_id] = to_save
    return to_save.bump(rev=rev + 1)


def apply_model_output(state: State, out: ModelOutput) -> State:
    """把「唯一不可信变量」转成可断言的状态迁移。

    final JSON 严格解析；解析出的 signal / bundle_ref / evidence 作为可信结论
    写入 spec 保留字段；usage 计入本 super-step（由 run 在调用处 add）。
    半截 JSON 不在此触发副作用——本内核只解析 final（partial 归 structured）。
    """
    spec = dict(state.spec or {})
    try:
        payload = json.loads(out.content) if out.content else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if isinstance(payload, dict):
        for key in ("signal", "reason", "bundle_ref", "evidence", "question", "tool", "args", "tier"):
            if key in payload:
                spec["__" + key] = payload[key]
    return state.bump(spec=spec)


def apply_tool_result(state: State, result_ref: str | None) -> State:
    spec = dict(state.spec or {})
    spec["__tool_result_ref"] = result_ref
    spec["__signal"] = "CALL_MODEL"  # 工具结果回灌后继续生成
    return state.bump(spec=spec)


def _acquire(deps: Deps, run_id: str) -> Lease | None:
    if deps.lease_provider is None:
        return None
    return deps.lease_provider.acquire(run_id, deps.owner, LEASE_TTL_S)


def run(session_id: str, run_id: str, user_input, deps: Deps) -> State:
    """§11.3 原文签名（追加 deps 注入参数；user_input 可为 Input 或 str）。"""
    text = getattr(user_input, "text", user_input) or ""
    grid = getattr(user_input, "grid", None)

    # 1. 装载状态（checkpoint.load 优先）
    state = deps.checkpoint_store.load(run_id)
    if state is None:
        state = State.initial(session_id, run_id, text, grid)

    # 2. 抢租约
    lease = _acquire(deps, run_id)
    trace_id = f"trace-{uuid4().hex[:12]}"

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

            # 5. 分支动作
            if decision.kind == "CALL_MODEL":
                out = deps.model.complete(decision.messages, tier=decision.tier, stream=True)
                if not isinstance(out, ModelOutput):
                    out = ModelOutput(content=str(out), usage=Usage())
                state = apply_model_output(state, out)
                state = meter.add(state, usage=out.usage, tier=out.tier)
            elif decision.kind == "CALL_TOOL":
                # 工具执行由 M03 tools.invoke 提供；本库核心只回灌引用
                ref = f"tool-result/{run_id}/{state.rev}"
                state = apply_tool_result(state, ref)
                state = meter.add(state, tool_calls=1)
            elif decision.kind == "REPAIR":
                # 回边，不新增 Run；计数与信号切换由 repair.feedback 统一负责
                from packages.agent_core.structured.repair import (
                    feedback as repair_feedback,
                )
                state = repair_feedback(state, decision.errors)
            elif decision.kind == "ASK_USER":
                state = state.bump(clarify_count=state.clarify_count + 1).pause()
            elif decision.kind == "RESUME_FROM_CLARIFICATION":
                state = state.resume()
            elif decision.kind == "AWAIT_INPUT":
                state = state.pause(reason=decision.reason_code)
            elif decision.kind in ("DONE", "HANDOFF", "FAILED"):
                now = deps.clock.now()
                state = state.terminate(
                    decision,
                    now=now,
                    bundle_ref=(state.spec or {}).get("__bundle_ref"),
                    evidence_ref=(state.spec or {}).get("__evidence"),
                )

            # 6. 每 super-step 同步落盘（rev 单调 + prev_hash 链）
            state = state.bump(seq=seq_no, iteration=state.iteration + 1)
            state = _persist(state, deps, decision.kind)

            # 7. 发事件（多种 kind：checkpoint / cost / 业务 kind / 终态）
            # 每个事件独立递增 seq（I8：连续、无重复、无倒序）
            cur_seq = seq_no

            def send(kind: str, payload: object = None, _state=state, _decision=decision) -> None:
                nonlocal cur_seq
                events_seq.emit(
                    cur_seq, _state.phase, _decision, bus=deps.event_bus,
                    session_id=session_id, run_id=run_id, trace_id=trace_id,
                    payload=payload, now=deps.clock.now(), kind=kind,
                )
                cur_seq += 1

            send("checkpoint", {"rev": state.rev, "node": decision.kind})
            send("cost", {"budget_used": dict(state.budget_used)})
            if state.terminal is not None:
                send("done" if state.terminal.kind == "DONE" else "error",
                     {"terminal": state.snapshot()["terminal"]})
            elif decision.kind == "ASK_USER":
                send("ask_user", {"question": (state.spec or {}).get("__question", "")})
            elif decision.kind == "AWAIT_INPUT":
                send("interrupted", {"reason_code": decision.reason_code})
            state = state.bump(seq=cur_seq - 1)

            # 8. 成本检查（四维硬上限，确定性中断）
            meter.check(state)

            # 9. 续租
            if lease is not None:
                lease.heartbeat()
                if deps.lease_provider is not None:
                    deps.lease_provider.heartbeat(run_id, deps.owner)

        # 10. 收尾：仅当循环因 MAX_ITER 退出且无终态时补写终态快照
        # （正常终态已在 super-step 落盘，不重复写）
        if state.iteration >= MAX_ITER and state.terminal is None:
            state = state.terminate(
                Decision("FAILED", reason_code="RETRY_EXHAUSTED"),
                now=deps.clock.now(),
                evidence_ref=f"max-iter/{run_id}",
            )
            state = _persist(state, deps, "final")
        return state
    finally:
        # 11. 释放租约
        if lease is not None:
            lease.release()
            if deps.lease_provider is not None:
                deps.lease_provider.release(run_id, deps.owner)
