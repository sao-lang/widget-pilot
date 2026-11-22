"""ADR-007 §3/§6/§7 Generator/Reviewer 功能角色协作循环（N>1 默认启用）。

Generator 按任务派生描述生成 spec/code（无终态权），Reviewer 只产出结构化
REVIEW_RESULT（无沙箱执行权、不改代码、无终态权），Router 中心路由，返修最多
MAX_REVIEW_ROUNDS=2 轮；同一 (task_id, role, artifact_hash) 出现 ≥2 次强制升级
人工（循环检测）。所有模型调用走真实端口，生产缺端点 fail-fast。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from uuid import uuid4

from packages.agent_core.types import Message, ModelOutput
from packages.contracts.enums import ReasonCode
from services.orchestrator.tasks.ports import (
    AgentMessage,
    EventEmitter,
    GeneratorPort,
    ReviewerPort,
    ReviewResult,
)
from services.orchestrator.tasks.router import Router, write_blackboard
from services.orchestrator.tasks.states import (
    MAX_REVIEW_ROUNDS,
    MessageType,
    Role,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoleOutcome:
    """一次角色循环结论（交 Coordinator fan-in，不自行裁决会话终态）。"""

    task_id: str
    passed: bool
    artifact_ref: str
    rounds: int
    reason_code: str = ""
    escalated: bool = False


def _artifact_hash(output: ModelOutput) -> str:
    """产物 hash（循环检测用，只 hash 内容不存正文）。"""
    return hashlib.sha256(output.content.encode("utf-8")).hexdigest()


def _review_prompt(result: ReviewResult) -> str:
    """Reviewer 反馈收窄为结论式短句（禁自由文本指令进 Generator prompt）。"""
    parts = [f"passed={str(result.passed).lower()}"]
    if result.reason_code:
        parts.append(f"reason={result.reason_code}")
    if result.evidence_ref:
        parts.append(f"evidence={result.evidence_ref}")
    return "；".join(parts)


def run_role_loop(
    *,
    session_id: str,
    task_id: str,
    description: str,
    trace_id: str,
    generator: GeneratorPort,
    reviewer: ReviewerPort,
    router: Router,
    events: EventEmitter,
    tier: str = "standard",
    max_rounds: int = MAX_REVIEW_ROUNDS,
) -> RoleOutcome:
    """单任务角色协作：Generator → Reviewer → (fail) REVISION_REQUEST → 重试。

    不变量：① 每轮消息经 Router；② Reviewer 反馈经 schema 收窄才回灌 Generator；
    ③ 同一产物 hash 重复即升级人工；④ 轮次上限后返回失败结论（不裁决会话）；
    ⑤ 每个角色实例有独立 agent_id/turn/role 归因事件。
    """
    seen_hashes: set[str] = set()
    artifact_ref = ""
    last_reason = ""
    agent_gen = f"gen-{uuid4()}"
    agent_rev = f"rev-{uuid4()}"

    for round_no in range(1, max_rounds + 1):
        gen_prompt = description
        if last_reason:
            gen_prompt = f"{description}\n评审反馈：{last_reason}"
        gen_messages = [
            Message(role="system", content="你是 Generator：按任务描述生成控件 spec/code。无权裁决终态。"),
            Message(role="user", content=gen_prompt),
        ]
        out = generator.generate(gen_messages, tier, False)
        current_hash = _artifact_hash(out)
        if current_hash in seen_hashes:
            reason = ReasonCode.HUMAN_REQUIRED.value
            router.route(
                AgentMessage(
                    session_id=session_id, task_id=task_id, agent_id=agent_gen,
                    from_role=Role.GENERATOR, to_role=Role.COORDINATOR,
                    message_type=MessageType.ESCALATE_HUMAN, turn=round_no,
                    trace_id=trace_id, payload={"reason_code": reason},
                )
            )
            events.emit(
                "agent_message",
                {"message_type": MessageType.ESCALATE_HUMAN.value, "reason_code": reason},
                task_id=task_id, role=Role.GENERATOR.value, turn=round_no, agent_id=agent_gen,
            )
            return RoleOutcome(task_id, False, artifact_ref, round_no, reason, escalated=True)
        seen_hashes.add(current_hash)

        # Router 写黑板（Generator 命名空间）；产物本体落对象存储由上游 V3 负责
        artifact_ref = out.content[:120] if out.content else ""
        write_blackboard(
            router.workspace, session_id, task_id, Role.GENERATOR,
            "spec", f"sandbox-ref://{current_hash}", round_no - 1,
        )
        review_req = AgentMessage(
            session_id=session_id, task_id=task_id, agent_id=agent_gen,
            from_role=Role.GENERATOR, to_role=Role.REVIEWER,
            message_type=MessageType.REVIEW_REQUEST, turn=round_no,
            trace_id=trace_id, payload={"artifact_ref": artifact_ref, "rev": round_no},
        )
        routed, route_reason = router.route(review_req)
        if not routed:
            # N3（review）：Router 拒绝的消息不得继续推进角色循环
            return RoleOutcome(task_id, False, artifact_ref, round_no, route_reason, escalated=True)
        events.emit(
            "agent_message",
            {"message_type": MessageType.REVIEW_REQUEST.value, "artifact_ref": artifact_ref},
            task_id=task_id, role=Role.GENERATOR.value, turn=round_no, agent_id=agent_gen,
        )

        review_messages = [
            Message(role="system", content="你是 Reviewer：只看产物与证据做结构化评审。无沙箱执行权、无终态权。"),
            Message(role="user", content=f"artifact={artifact_ref}\n任务={description}"),
        ]
        review = reviewer.review(review_messages, tier, False)
        review_msg = AgentMessage(
            session_id=session_id, task_id=task_id, agent_id=agent_rev,
            from_role=Role.REVIEWER, to_role=Role.GENERATOR,
            message_type=MessageType.REVIEW_RESULT, turn=round_no,
            trace_id=trace_id,
            payload={
                "passed": review.passed,
                "reason_code": review.reason_code,
                "evidence_ref": review.evidence_ref,
                "artifact_ref": review.artifact_ref,
                "score": review.score,
            },
        )
        ok, reason = router.review_result(review_msg)
        if not ok:
            return RoleOutcome(task_id, False, artifact_ref, round_no, reason, escalated=True)
        events.emit(
            "review",
            {
                "passed": review.passed,
                "reason_code": review.reason_code,
                "evidence_ref": review.evidence_ref,
                "artifact_ref": review.artifact_ref,
                "score": review.score,
            },
            task_id=task_id, role=Role.REVIEWER.value, turn=round_no, agent_id=agent_rev,
        )
        if review.passed:
            router.route(
                AgentMessage(
                    session_id=session_id, task_id=task_id, agent_id=agent_rev,
                    from_role=Role.REVIEWER, to_role=Role.COORDINATOR,
                    message_type=MessageType.FINAL, turn=round_no,
                    trace_id=trace_id, payload={"artifact_ref": artifact_ref},
                )
            )
            return RoleOutcome(task_id, True, artifact_ref, round_no)
        last_reason = _review_prompt(review)
        if round_no < max_rounds:
            router.route(
                AgentMessage(
                    session_id=session_id, task_id=task_id, agent_id=agent_gen,
                    from_role=Role.COORDINATOR, to_role=Role.GENERATOR,
                    message_type=MessageType.REVISION_REQUEST, turn=round_no + 1,
                    trace_id=trace_id, payload={"reason_code": review.reason_code, "note": last_reason},
                )
            )
            events.emit("delegate", {"action": MessageType.REVISION_REQUEST.value}, task_id=task_id,
                        role=Role.COORDINATOR.value, turn=round_no + 1)

    return RoleOutcome(
        task_id, False, artifact_ref, max_rounds, ReasonCode.RETRY_EXHAUSTED.value
    )


__all__ = ["RoleOutcome", "run_role_loop"]
