"""M03 decide() —— 唯一的状态迁移入口，纯函数（§11.3 / §3.4 A）。

只读取 State 的确定性字段，返回一个 Decision；不调模型、不落盘、不发事件、
不读时钟。同一 State 输入必得同一 Decision 输出（T1 快照断言）。

模型这个「唯一不可信变量」被隔离在 run.py 的 CALL_MODEL 分支；decide 只读
``State.spec`` 中的可信结论槽位（:class:`SpecState` 的显式字段）——这些字段由
apply_model_output / apply_capability_result / repair.feedback / 澄清端点写入。
类型改造后不再有 ``spec["__signal"]`` 这类动态键读取，全部为属性访问。
"""
from __future__ import annotations

from packages.agent_core.constants import MAX_CLARIFY, MAX_ITER, MAX_REPAIR
from packages.agent_core.types import Decision, DecisionKind, Message, SpecState, State
from packages.contracts.enums import ReasonCode, SessionState


def _signal(spec: SpecState) -> DecisionKind:
    """读取信号槽（外部/模型写入的动态边界），经 coerce 归一为枚举，未知值回退 CALL_MODEL。"""
    return DecisionKind.coerce(spec.signal)


def _reason(spec: SpecState) -> str | None:
    """读取信号伴随的 ReasonCode（外部 wire 值，空串视为未写）。"""
    return spec.reason or None


def decide(state: State) -> Decision:
    """确定性状态迁移（纯函数）。判定优先级固定，便于逐行讲清与回放。"""
    spec = state.spec

    # 0. 已终态：防御性返回 FAILED（正常由 while 条件拦截，不应被调用）
    if state.terminal is not None:
        return Decision(
            kind=DecisionKind.FAILED,
            reason_code=state.terminal.reason_code or ReasonCode.HUMAN_REQUIRED,
        )

    # 1. 挂起态：有澄清答案 → 续跑；否则 → 等待输入
    if state.phase == SessionState.AWAITING_INPUT:
        # 用户已回填澄清答案：脱离挂起态，把答案交给下一轮继续生成
        if state.clarification_answer is not None:
            return Decision(DecisionKind.RESUME_FROM_CLARIFICATION)
        # 仍无答案：维持挂起，等待澄清端点注入，不消耗迭代预算
        return Decision(DecisionKind.AWAIT_INPUT, reason_code=_reason(spec) or ReasonCode.CLARIFICATION_REQUIRED)

    # 2. 迭代上限（MAX_ITER）：确定性中断
    if state.iteration >= MAX_ITER:
        return Decision(DecisionKind.FAILED, reason_code=ReasonCode.RETRY_EXHAUSTED)

    sig = _signal(spec)

    # 3. REPAIR 回边：业务失败，≤MAX_REPAIR；超限转 HANDOFF
    if sig == DecisionKind.REPAIR:
        # 修复次数已达上限：不再重试，升级为人工接管，避免死循环
        if state.repair_count >= MAX_REPAIR:
            return Decision(DecisionKind.HANDOFF, reason_code=ReasonCode.RETRY_EXHAUSTED, errors=spec.errors)
        # 仍有修复预算：携带本轮错误反馈走回边重试
        return Decision(DecisionKind.REPAIR, errors=spec.errors, reason_code=_reason(spec))

    # 4. ASK_USER（v1.3）：澄清提问 ≤MAX_CLARIFY；超限转 HANDOFF
    if sig == DecisionKind.ASK_USER:
        # 澄清次数已达上限：避免无限追问，转人工接管
        if state.clarify_count >= MAX_CLARIFY:
            return Decision(DecisionKind.HANDOFF, reason_code=ReasonCode.CLARIFICATION_LIMIT)
        # 仅在有具体问题时才发消息；问题文本为空时只挂起、不发空消息
        messages = [Message(role="assistant", content=spec.question)] if spec.question else []
        return Decision(DecisionKind.ASK_USER, messages=messages, reason_code=ReasonCode.CLARIFICATION_REQUIRED)

    # 5. AWAIT_INPUT：规格缺失 / 意图越界等业务挂起
    if sig == DecisionKind.AWAIT_INPUT:
        return Decision(DecisionKind.AWAIT_INPUT, reason_code=_reason(spec) or ReasonCode.SPEC_MISSING)

    # 6. 终态信号：三种终态并列，未写 reason 时统一回退到 HUMAN_REQUIRED
    if sig == DecisionKind.DONE:
        # 目标达成：正常终态，无需 reason_code
        return Decision(DecisionKind.DONE)
    if sig == DecisionKind.FAILED:
        # 失败终态：优先保留信号携带的 ReasonCode 供归因
        return Decision(DecisionKind.FAILED, reason_code=_reason(spec) or ReasonCode.HUMAN_REQUIRED)
    if sig == DecisionKind.HANDOFF:
        # 转人工终态：同样保留上游 ReasonCode，便于前端展示与评测归因
        return Decision(DecisionKind.HANDOFF, reason_code=_reason(spec) or ReasonCode.HUMAN_REQUIRED)

    # 7. 能力加载（ADR-003）：确定性授权交给 tools.load()
    if sig == DecisionKind.LOAD_CAPABILITY:
        return Decision(
            DecisionKind.LOAD_CAPABILITY,
            capability_id=spec.capability_id,
            level=spec.level,
            args=spec.args,
            surface=spec.surface,
        )

    # 8. 默认：唯一调模型的分支
    messages = spec.messages or [Message(role="user", content=spec.intent)]
    return Decision(DecisionKind.CALL_MODEL, messages=messages, tier=spec.tier)
