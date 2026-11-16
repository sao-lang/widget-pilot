"""M03 agent_core 共享数据类（§4.1 / §5.1 / §6.2 / §4.2）。

State / Decision / Terminal 逐字对齐 §11.3；其余为让库可调用的最小补全
（【契约约定】/【推导·需评审】已在文档标注）。所有跨进程客户端以 Protocol
注入（见 injection.py），本文件只定义纯数据结构，不 import 任何具体客户端。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel

from packages.contracts.enums import (
    ModelTier,
    SessionState,
)

# ---------------------------------------------------------------------------
# 决策与状态（§5.1 / §6.2，逐字对齐）
# ---------------------------------------------------------------------------

DecisionKind = Literal[
    "CALL_MODEL", "CALL_TOOL", "REPAIR", "AWAIT_INPUT", "DONE", "HANDOFF",
    "FAILED", "ASK_USER", "RESUME_FROM_CLARIFICATION",
]


class Decision(NamedTuple):
    """§11.3 逐字：纯函数 decide() 的输出，9 种 kind（v1.3 新增 2 种）。"""

    kind: DecisionKind
    messages: list[Message] = []  # noqa: RUF012 - §11.3 逐字默认值，NamedTuple 不可变故共享安全
    tier: ModelTier = "standard"
    tool: str | None = None
    args: dict[str, Any] = {}  # noqa: RUF012 - 同上，逐字语义
    surface: str = "generation"
    errors: list[str] = []  # noqa: RUF012 - 同上，逐字语义
    reason_code: str | None = None


@dataclass(frozen=True)
class Terminal:
    """§5.1【推导·需评审】已固化：DONE 必带 bundle_ref；FAILED/HANDOFF 必带 reason_code + evidence_ref。"""

    kind: Literal["DONE", "HANDOFF", "FAILED"]
    reason_code: str | None = None
    bundle_ref: str | None = None
    evidence_ref: str | None = None
    finished_at: str = ""

    def __post_init__(self) -> None:
        if self.kind == "DONE" and not self.bundle_ref:
            raise ValueError("DONE terminal must carry bundle_ref")
        if self.kind in ("FAILED", "HANDOFF") and not (self.reason_code and self.evidence_ref):
            raise ValueError(f"{self.kind} terminal must carry reason_code and evidence_ref")


@dataclass
class State:
    """§11.3 逐字：运行时状态。字段顺序与文档一致，不得新增业务字段。"""

    session_id: str
    run_id: str
    rev: int
    seq: int
    phase: SessionState
    iteration: int
    repair_count: int
    clarify_count: int
    spec: dict | None
    diff_ref: str | None
    excluded_approaches: list[str]
    budget_used: dict
    tier_history: list
    last_error_fingerprint: str | None
    clarification_answer: str | None
    terminal: Terminal | None

    @classmethod
    def initial(
        cls,
        session_id: str,
        run_id: str,
        text: str = "",
        grid: str | None = None,
    ) -> State:
        """【推导·需评审】§3.3 补全：从用户输入初始化。grid 可空（v1.3 D-6 不推断）。"""
        spec = {"intent": text}
        if grid is not None:
            spec["grid"] = grid
        return cls(
            session_id=session_id,
            run_id=run_id,
            rev=0,
            seq=0,
            phase=SessionState.RUNNING,
            iteration=0,
            repair_count=0,
            clarify_count=0,
            spec=spec,
            diff_ref=None,
            excluded_approaches=[],
            budget_used={"tokens": 0, "iterations": 0, "tool_calls": 0, "sandbox_minutes": 0.0},
            tier_history=[],
            last_error_fingerprint=None,
            clarification_answer=None,
            terminal=None,
        )

    def pause(self, reason: str | None = None) -> State:
        """AWAIT_INPUT / ASK_USER → AWAITING_INPUT，挂起不占计算资源。"""
        return replace(self, phase=SessionState.AWAITING_INPUT)

    def resume(self) -> State:
        """RESUME_FROM_CLARIFICATION → RUNNING 续跑。"""
        return replace(self, phase=SessionState.RUNNING)

    def with_answer(self, answer: str) -> State:
        """澄清答案回注 clarification_answer 槽位。"""
        return replace(self, clarification_answer=answer, phase=SessionState.RUNNING)

    def terminate(
        self,
        decision: Decision,
        *,
        now: datetime | None = None,
        bundle_ref: str | None = None,
        evidence_ref: str | None = None,
    ) -> State:
        """DONE/HANDOFF/FAILED → 写终态。

        now 由调用方注入（decide 不读时钟）；bundle_ref/evidence_ref 由调用方
        按 decision.kind 提供，终态约束在 Terminal.__post_init__ 校验。
        """
        ts = (now or datetime.now(UTC)).isoformat()
        kind = decision.kind if decision.kind in ("DONE", "HANDOFF", "FAILED") else "FAILED"
        terminal = Terminal(
            kind=kind,
            reason_code=decision.reason_code,
            bundle_ref=bundle_ref,
            evidence_ref=evidence_ref,
            finished_at=ts,
        )
        phase = SessionState.COMPLETED if kind == "DONE" else SessionState.FAILED
        return replace(self, phase=phase, terminal=terminal)

    def bump(self, **changes: Any) -> State:
        """生成一个新 State 副本（不可变式更新），供各层 apply_* 使用。"""
        return replace(self, **changes)

    def snapshot(self) -> dict[str, Any]:
        """可断言快照（T1/T20/T26）：序列化为纯 dict，含终态。"""
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "rev": self.rev,
            "seq": self.seq,
            "phase": self.phase,
            "iteration": self.iteration,
            "repair_count": self.repair_count,
            "clarify_count": self.clarify_count,
            "spec": self.spec,
            "diff_ref": self.diff_ref,
            "excluded_approaches": list(self.excluded_approaches),
            "budget_used": dict(self.budget_used),
            "tier_history": list(self.tier_history),
            "last_error_fingerprint": self.last_error_fingerprint,
            "clarification_answer": self.clarification_answer,
            "terminal": None if self.terminal is None else {
                "kind": self.terminal.kind,
                "reason_code": self.terminal.reason_code,
                "bundle_ref": self.terminal.bundle_ref,
                "evidence_ref": self.terminal.evidence_ref,
                "finished_at": self.terminal.finished_at,
            },
        }


# ---------------------------------------------------------------------------
# 消息与模型（§4.1 L1/L2）
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Input:
    """loop.run 入参（§11.3）。text 即归一化前描述；grid 可空（v1.3 D-6）。"""

    text: str = ""
    grid: str | None = None
    attachments: list[str] = field(default_factory=list)


@dataclass
class Usage:
    """usage 归一结果（跨供应商字段不同，统一到此）。"""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ModelOutput:
    """model.complete 返回（含可控半截 JSON / 越权代码用于故障注入）。"""

    content: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    tier: ModelTier = "standard"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Delta:
    """stream.parse 输出的单个增量。"""

    text: str = ""
    done: bool = False


@dataclass
class StreamBuffer:
    """SSE 半帧缓存。"""

    text: str = ""
    deltas: list[Delta] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 结构化输出（§4.1 L5/L6/L7）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairError:
    """§3.4 D【推导·需评审】固化：{field, expected, got}。"""

    field: str
    expected: str
    got: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "expected": self.expected, "got": self.got}


@dataclass
class ValidationResult:
    ok: bool
    data: dict | None = None
    errors: list[RepairError] = field(default_factory=list)
    code: str | None = None


@dataclass
class PartialObject:
    """partial.parse 结果：complete=False 表示 JSON 未闭合（流中）。"""

    value: dict[str, Any] = field(default_factory=dict)
    complete: bool = False
    raw: str = ""


# ---------------------------------------------------------------------------
# 工具（§4.1 L8/L9/L10，§6.2 ToolSpec 逐字）
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    surfaces: set[str]
    handler: Callable[..., Any]
    max_output_chars: int


@dataclass
class ToolResult:
    """§6.2【推导·需评审】固化：output 已截断。"""

    ok: bool
    output: str
    truncated: bool = False
    error_code: str | None = None


# ---------------------------------------------------------------------------
# 护栏（§4.1 L24/L25/L26）
# ---------------------------------------------------------------------------

@dataclass
class GuardVerdict:
    hit: bool
    guard: str = ""
    hit_type: str | None = None
    criterion: str | None = None
    source_ref: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class GuardHit:
    """guard_hit 表的一条留证（类型 / 判据 / 原文引用）。"""

    guard: str
    hit_type: str
    criterion: str
    source_ref: str
    session_id: str = ""
    run_id: str = ""
    seq: int = 0


@dataclass
class CapabilityCall:
    name: str
    surface: str = "generation"


@dataclass
class Manifest:
    """WidgetBundle.manifest 关键字段的能力比对子集（§6.2）。"""

    capabilities: set[str] = field(default_factory=set)
    import_allowlist: tuple[str, ...] = ("react", "@widget/bridge")
    exports_default: str = "Widget"
    spec_version: str = "1.0"
    retrieval_snapshot: str = ""
    model: str = ""
    model_tier: str = "standard"
    prompt_version: str = ""
    recipe_version: str = ""


# ---------------------------------------------------------------------------
# 事件（§4.2 AgentEvent 逐字）
# ---------------------------------------------------------------------------

@dataclass
class AgentEvent:
    seq: int
    session_id: str
    run_id: str
    ts: str
    kind: str
    phase: str
    payload: Any
    trace_id: str

    def to_dict(self) -> dict[str, Any]:
        """契约字段名为 camelCase（sessionId/runId/traceId）。"""
        return {
            "seq": self.seq,
            "sessionId": self.session_id,
            "runId": self.run_id,
            "ts": self.ts,
            "kind": self.kind,
            "phase": self.phase,
            "payload": self.payload,
            "traceId": self.trace_id,
        }


@dataclass
class ClarificationRequest:
    """v1.3 ADR-001 D-7：模型产出的结构化澄清问题。"""

    question_id: str
    question: str
    options: list[str] = field(default_factory=list)
    allow_text: bool = True


# ---------------------------------------------------------------------------
# 上下文 / 预算 / 评测（§4.1 L11/L12/L13/L27-L29）
# ---------------------------------------------------------------------------

@dataclass
class SlotConfig:
    """固定槽位装配配置；顺序固定保住 prompt caching。"""

    include_memory: bool = True
    include_retrieval: bool = True
    max_tokens: int = 8000
    now: datetime | None = None


@dataclass
class Candidate:
    """检索候选（向量 / BM25 结果统一形态）。"""

    doc_id: str
    text: str
    score: float
    source: str = "vector"  # vector / bm25


@dataclass
class Trajectory:
    """eval/replay 输出：可断言的确定性轨迹（T26 快照相等）。"""

    events: list[dict[str, Any]] = field(default_factory=list)
    terminal: dict[str, Any] | None = None
    fingerprint: dict[str, str] = field(default_factory=dict)


@dataclass
class EvalCase:
    case_id: str
    input: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    fingerprint: dict[str, str] = field(default_factory=dict)


@dataclass
class Score:
    case_id: str
    dimensions: dict[str, float] = field(default_factory=dict)
    passed: bool = False

    @property
    def total(self) -> float:
        """七维度加权平均（归一化到 [0,1]）。"""
        if not self.dimensions:
            return 0.0
        return sum(self.dimensions.values()) / len(self.dimensions)


@dataclass
class GateResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def label(self) -> str:
        return "GATE OK" if self.ok else "GATE FAIL"
