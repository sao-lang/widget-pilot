"""M03 decide() —— 唯一的状态迁移入口，纯函数（§11.3 / §3.4 A）。

只读取 State 的确定性字段，返回一个 Decision；不调模型、不落盘、不发事件、
不读时钟。同一 State 输入必得同一 Decision 输出（T1 快照断言）。

模型这个「唯一不可信变量」被隔离在 run.py 的 CALL_MODEL 分支；decide 用
`spec["__signal"]` 等保留字段承载「上一步已写入的可信结论」——这些字段由
apply_model_output / apply_tool_result / repair.feedback / 澄清端点写入，
decide 只读，不产生。信号字段以 `__` 前缀保留，不属于 WidgetSpec 业务字段。
"""
from __future__ import annotations

from packages.agent_core.constants import MAX_CLARIFY, MAX_ITER, MAX_REPAIR
from packages.agent_core.types import Decision, Message, State
from packages.contracts.enums import SessionState


def _signal(state: State) -> str:
    return (state.spec or {}).get("__signal", "")


def _reason(state: State) -> str | None:
    return (state.spec or {}).get("__reason")


def decide(state: State) -> Decision:
    """确定性状态迁移（纯函数）。判定优先级固定，便于逐行讲清与回放。"""
    spec = state.spec or {}

    # 0. 已终态：防御性返回 FAILED（正常由 while 条件拦截，不应被调用）
    if state.terminal is not None:
        return Decision(
            kind="FAILED",
            reason_code=state.terminal.reason_code or "HUMAN_REQUIRED",
        )

    # 1. 挂起态：有澄清答案 → 续跑；否则 → 等待输入
    if state.phase == SessionState.AWAITING_INPUT:
        if state.clarification_answer is not None:
            return Decision("RESUME_FROM_CLARIFICATION")
        return Decision("AWAIT_INPUT", reason_code=_reason(state) or "CLARIFICATION_REQUIRED")

    # 2. 迭代上限（MAX_ITER）：确定性中断
    if state.iteration >= MAX_ITER:
        return Decision("FAILED", reason_code="RETRY_EXHAUSTED")

    sig = _signal(state)

    # 3. REPAIR 回边：业务失败，≤MAX_REPAIR；超限转 HANDOFF
    if sig == "REPAIR":
        errors = spec.get("__errors", [])
        if state.repair_count >= MAX_REPAIR:
            return Decision("HANDOFF", reason_code="RETRY_EXHAUSTED", errors=errors)
        return Decision("REPAIR", errors=errors, reason_code=_reason(state))

    # 4. ASK_USER（v1.3）：澄清提问 ≤MAX_CLARIFY；超限转 HANDOFF
    if sig == "ASK_USER":
        if state.clarify_count >= MAX_CLARIFY:
            return Decision("HANDOFF", reason_code="CLARIFICATION_LIMIT")
        question = spec.get("__question", "")
        messages = [Message(role="assistant", content=question)] if question else []
        return Decision("ASK_USER", messages=messages, reason_code="CLARIFICATION_REQUIRED")

    # 5. AWAIT_INPUT：规格缺失 / 意图越界等业务挂起
    if sig == "AWAIT_INPUT":
        return Decision("AWAIT_INPUT", reason_code=_reason(state) or "SPEC_MISSING")

    # 6. 终态信号
    if sig == "DONE":
        return Decision("DONE")
    if sig == "FAILED":
        return Decision("FAILED", reason_code=_reason(state) or "HUMAN_REQUIRED")
    if sig == "HANDOFF":
        return Decision("HANDOFF", reason_code=_reason(state) or "HUMAN_REQUIRED")

    # 7. 工具调用：确定性授权交给 invoke()
    if sig == "CALL_TOOL":
        return Decision(
            "CALL_TOOL",
            tool=spec.get("__tool"),
            args=spec.get("__args", {}),
            surface=spec.get("__surface", "generation"),
        )

    # 8. 默认：唯一调模型的分支
    messages = spec.get("__messages", [Message(role="user", content=spec.get("intent", ""))])
    tier = spec.get("__tier", "standard")
    return Decision("CALL_MODEL", messages=messages, tier=tier)
