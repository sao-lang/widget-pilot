"""purity-exempt-file: 本文件含 MessageStore/MessageWorkspace Protocol（方法体 `...` 为接口声明）。

ADR-007 §4/§5 中心路由 + 版本化黑板（零模型确定性状态机）。

所有 agent 消息必经 Router，不直接互发。Router 做：schema 校验 → 确定性注入/PII
形状检查 → 目标角色解析 → 预算扣减 → 落库 → 投递。禁自由文本指令，payload 只
允许「引用 + 枚举 + 结论式短句」。

黑板 agent_workspace 只存对象存储引用 + hash，不存大对象本体；写时 optimistic
lock（UPDATE WHERE rev=?），冲突由上层最多重试 2 次（ADR-007 §5）。
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

from packages.agent_core.types import JsonValue
from services.guardrails.patterns.injection import RULES as INJECTION_RULES
from services.orchestrator.tasks.ports import AgentMessage
from services.orchestrator.tasks.states import (
    MAX_AGENT_TURNS,
    MAX_DELEGATE_DEPTH,
    MessageType,
    Role,
)

logger = logging.getLogger(__name__)

_ALLOWED_MESSAGE_KEYS: frozenset[str] = frozenset(
    {
        "reason_code", "evidence_ref", "artifact_ref", "score", "passed",
        "task_id", "turn", "rev", "value_ref", "note",
    }
)
_MAX_NOTE_CHARS = 120
"""结论式短句上限（防跨 agent 消息成为提示注入通道）。"""


class MessageWorkspace(Protocol):
    """Router 所需的最小黑板端口（由 PgAgentWorkspace 实现）。"""

    def read(self, session_id: str, key: str) -> object | None: ...
    def write(
        self, session_id: str, key: str, value_ref: str, writer_role: str, expected_rev: int
    ) -> None: ...
    def create_if_missing(
        self, session_id: str, key: str, value_ref: str, writer_role: str
    ) -> None: ...


class MessageStore(Protocol):
    """Router 所需的最小消息留证端口（由 PgAgentMessageStore 实现）。"""

    def append(self, message: AgentMessage) -> None: ...


def _canonical(payload: JsonValue) -> str:
    """确定性 JSON 串（hash 链与 trace 用）。"""
    import json
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def validate_message(message: AgentMessage) -> tuple[bool, str]:
    """消息 schema 收窄 + 确定性注入/PII 形状检查（跨 agent 消息是不可信输入）。"""
    if not message.task_id or not message.session_id or not message.agent_id:
        return False, "INPUT_INVALID: identity"
    if not isinstance(message.payload, dict):
        return False, "INPUT_INVALID: payload"
    for key, value in message.payload.items():
        if key not in _ALLOWED_MESSAGE_KEYS:
            return False, f"INPUT_INVALID: key={key}"
        if isinstance(value, str):
            if len(value) > _MAX_NOTE_CHARS:
                return False, f"INPUT_INVALID: oversized {key}"
            if any(re.search(rule.regex, value) for rule in INJECTION_RULES):
                return False, f"GUARD_INPUT_HIT: {key}"
    if message.message_type not in set(MessageType):
        return False, "INPUT_INVALID: message_type"
    return True, ""


def workspace_key(role: Role | str, task_id: str, suffix: str) -> str:
    """命名空间隔离：Generator 只写 generator:{task_id}:*，Reviewer 只写 review:{task_id}:*。"""
    role_value = role.value if isinstance(role, Role) else str(role)
    if role_value not in ("generator", "reviewer"):
        return f"system:{task_id}:{suffix}"
    return f"{role_value}:{task_id}:{suffix}"


def assert_write_scope(role: Role, key: str) -> bool:
    """写权限校验：角色只能写自己的命名空间（Reviewer 无沙箱/终态权）。"""
    if role is Role.GENERATOR:
        return key.startswith("generator:") and ":system:" not in key
    if role is Role.REVIEWER:
        return key.startswith("review:") and ":system:" not in key
    return False


def write_blackboard(
    workspace: MessageWorkspace,
    session_id: str,
    task_id: str,
    role: Role,
    suffix: str,
    value_ref: str,
    expected_rev: int,
    *,
    max_retries: int = 2,
) -> None:
    """黑板写入（乐观锁，最多重试 2 次），Namespace 越权直接抛错。"""
    key = workspace_key(role, task_id, suffix)
    if not assert_write_scope(role, key):
        raise PermissionError(f"workspace namespace denied: {role} -> {key}")
    rev = expected_rev
    for attempt in range(max_retries + 1):
        try:
            workspace.write(session_id, key, value_ref, role.value, rev)
            return
        except Exception as exc:
            if attempt >= max_retries:
                raise
            logger.warning("workspace rev conflict retry=%s key=%s: %s", attempt + 1, key, exc)
            current = workspace.read(session_id, key)
            if current is None:
                workspace.create_if_missing(session_id, key, value_ref, role.value)
                return
            rev = int(getattr(current, "rev", 0)) + 1


class Router:
    """中心路由：校验 → 预算 → 落库 → 投递（无模型调用权，延续 ADR-006 O1）。"""

    def __init__(
        self,
        store: MessageStore,
        workspace: MessageWorkspace,
        *,
        max_depth: int = MAX_DELEGATE_DEPTH,
    ) -> None:
        self._store = store
        self._workspace = workspace
        self._max_depth = max_depth

    def route(self, message: AgentMessage) -> tuple[bool, str]:
        """校验并落库一条消息；返回（是否投递, 原因）。"""
        ok, reason = validate_message(message)
        if not ok:
            logger.warning("agent message rejected: %s", reason)
            return False, reason
        if message.turn > MAX_AGENT_TURNS:
            return False, "BUDGET_EXHAUSTED: turn"
        self._store.append(message)
        return True, ""

    def review_result(self, message: AgentMessage) -> tuple[bool, str]:
        """Reviewer 反馈对 Generator 等同不可信输入：必须再过一层 schema 收窄。"""
        if message.message_type is not MessageType.REVIEW_RESULT:
            return False, "INPUT_INVALID: not REVIEW_RESULT"
        if message.payload.get("passed") is False and not message.payload.get("reason_code"):
            return False, "SPEC_MISSING: review reason"
        return self.route(message)

    def can_delegate(self, depth: int) -> bool:
        """委托深度上限（防无限互评）。"""
        return 0 <= depth <= self._max_depth

    @property
    def workspace(self) -> MessageWorkspace:
        """黑板端口（角色层写入用；Router 仍保留唯一路由权）。"""
        return self._workspace


def message_hash(message: AgentMessage) -> str:
    """消息 hash（循环检测与留证用，与 PgAgentMessageStore.hash 链同算法）。"""
    return hashlib.sha256(_canonical(message.payload).encode("utf-8")).hexdigest()


__all__ = [
    "MessageStore",
    "MessageWorkspace",
    "Router",
    "assert_write_scope",
    "message_hash",
    "validate_message",
    "workspace_key",
    "write_blackboard",
]
