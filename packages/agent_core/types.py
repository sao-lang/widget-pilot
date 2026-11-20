"""M03 agent_core 共享数据类（§4.1 / §5.1 / §6.2 / §4.2）。

State / Decision / Terminal 逐字对齐 §11.3；其余为让库可调用的最小补全
（【契约约定】/【推导·需评审】已在文档标注）。所有跨进程客户端以 Protocol
注入（见 injection.py），本文件只定义纯数据结构，不 import 任何具体客户端。

类型设计约定（本次类型改造）：
- **确定字段一律显式声明**：旧实现把 ``State.spec`` 当作 ``dict`` 使用，靠
  ``__signal`` / ``__reason`` / ``__errors`` 等字符串键存取。这些键的真实集合
  是确定的，现统一收敛为 :class:`SpecState` 的显式字段（Pydantic 模型）。
- **不确定内容聚集到单一字段**：工具参数、provider 原始响应、事件 payload 等
  结构由外部决定，统一使用 :data:`JsonObject` / :data:`JsonValue`，并在
  :class:`SpecState.extras` 留出唯一扩展位，不再散落任意动态键。
- **零裸 Any**：对外接口只出现具体类型或 JSON 值别名。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple, TypedDict, Unpack, cast

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.enums import ModelTier, SessionState

# ---------------------------------------------------------------------------
# JSON 值别名：外部（provider / 事件 / schema）决定的不确定结构只用这两个别名承载
# ---------------------------------------------------------------------------

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
"""合法的 JSON 值递归联合：None / 布尔 / 数值 / 字符串 / 数组 / 对象。

用于模型输出原文、事件 payload 等「结构由外部决定」的数据，取代裸 ``Any``。
"""

type JsonObject = dict[str, JsonValue]
"""JSON 对象（键值均为 :data:`JsonValue`），作为不确定结构的统一聚集类型。"""


def _json_str(value: JsonValue | None) -> str | None:
    """把 JSON 边界的宽泛值收窄为 str|None（跨天恢复反序列化用）。

    非字符串值（异常的历史快照）返回 None 而非抛错，保持恢复路径 fail-safe。
    """
    return value if isinstance(value, str) else None


type RepairIssue = str | JsonObject
"""一条校验错误：可为简短字符串，或 ``{field, expected, got}`` 结构化字典。"""


# ---------------------------------------------------------------------------
# 确定字段标准槽位：取代旧的 spec["__xxx"] 动态键
# ---------------------------------------------------------------------------

class SpecState(BaseModel):
    """一次运行的``会话规格 + 可信结论槽位``（Pydantic 严格校验）。

    旧实现把这些字段塞进 ``State.spec`` 这个 dict，用 ``__signal`` / ``__reason``
    等键名动态读写；键名集合其实是确定的（由 decide / apply_model_output /
    repair.feedback / 澄清端点写入），因此全部提升为显式类型字段。

    字段来源约定：
    - ``intent`` / ``grid`` 来自用户输入（§3.3 D-6：grid 可空，不推断）；
    - ``signal`` 等结论槽位由可信代码写入，decide 只读；
    - ``extras`` 是**唯一**的扩展位，承接 WidgetSpec 等业务侧不确定字段。
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    """允许原地赋值校验，且拒绝未知字段——从根上杜绝动态键写回 spec。

    不确定内容只能显式放入 :attr:`extras`，不能作为顶层键混入。
    """

    # ---- 业务输入字段（来自用户，非模型可信结论）----
    intent: str = ""
    """归一化前的用户意图描述；State.initial 由 text 注入。"""
    grid: str | None = None
    """用户选择的规格（如 ``4x4``）；v1.3 D-6 允许为空，模型不得推断。"""

    # ---- 可信结论槽位（旧 __signal 等动态键，decide 只读）----
    signal: str = ""
    """上一步写入的信号（CALL_MODEL / REPAIR / DONE / ASK_USER ...）。

    旧键名 ``spec["__signal"]``。空串表示默认走唯一调模型分支。
    """
    reason: str | None = None
    """伴随信号的 ReasonCode；旧键名 ``spec["__reason"]``。"""
    errors: list[RepairIssue] = Field(default_factory=list)
    """结构化校验错误列表（字符串或 {field,expected,got}）；旧键 ``spec["__errors"]``。"""
    tool: str | None = None
    """CALL_TOOL 的工具名；旧键名 ``spec["__tool"]``。"""
    args: JsonObject = Field(default_factory=dict)
    """工具参数（结构由工具 args_model 决定，属不确定内容）；旧键 ``__args``。"""
    surface: str = "generation"
    """工具调用表面（generation / widget-runtime）；旧键 ``__surface``。"""
    tier: ModelTier = ModelTier.STANDARD
    """模型档位（cheap / standard / frontier）；旧键 ``__tier``。"""
    messages: list[Message] = Field(default_factory=list)
    """显式注入的对话消息；旧键 ``__messages``。为空时 decide 用 intent 造一条。"""
    bundle_ref: str | None = None
    """DONE 终态的产物引用；旧键 ``__bundle_ref``。"""
    evidence: str | None = None
    """FAILED/HANDOFF 的证据引用；旧键 ``__evidence``。"""
    question: str = ""
    """ASK_USER 的澄清问题文本；旧键 ``__question``。"""
    tool_result_ref: str | None = None
    """工具结果回灌的对象存储引用；旧键 ``__tool_result_ref``。"""

    # ---- 唯一不确定扩展位 ----
    extras: JsonObject = Field(default_factory=dict)
    """业务侧不确定字段的唯一聚集处（如完整 WidgetSpec 键值）。

    不再允许向 spec 顶层新增任意动态键；所有新字段先判定是否确定，确定则提升
    为显式字段，不确定则放入本字段。
    """


# ---------------------------------------------------------------------------
# TypedDict：确定键集合、运行时仍为 dict（序列化 / 分账边界）
# ---------------------------------------------------------------------------

class BudgetUsage(TypedDict):
    """四维预算用量：键集合确定，运行时保持 dict 以直接 JSON 序列化。"""

    tokens: int
    """累计 token 数（input + output）。"""
    iterations: int
    """累计 super-step 轮次。"""
    tool_calls: int
    """累计工具调用次数。"""
    sandbox_minutes: float
    """累计沙箱运行分钟数。"""


class BudgetLimits(TypedDict):
    """四维预算硬上限，键与 :class:`BudgetUsage` 一一对应。"""

    tokens: float
    """token 上限。"""
    iterations: float
    """轮次上限（复用 MAX_ITER）。"""
    tool_calls: float
    """工具调用上限。"""
    sandbox_minutes: float
    """沙箱分钟上限。"""


class FingerprintInfo(TypedDict, total=False):
    """复现指纹五要素（§6.4 FINGERPRINT_PARTS），键集合确定。"""

    prompt_version: str
    """prompt 版本。"""
    recipe_version: str
    """生成配方版本。"""
    retrieval_snapshot: str
    """检索索引快照标识。"""
    model: str
    """模型标识。"""
    model_tier: str
    """模型档位。"""


class ScoreDimensions(TypedDict):
    """rubric 七维度评分，键集合确定（失败模式加权，§9.5）。"""

    task_success: float
    """任务是否成功。"""
    first_pass: float
    """一次生成通过程度。"""
    self_correction: float
    """自纠正质量。"""
    safety: float
    """安全一票否决位（0 即否决）。"""
    efficiency: float
    """效率分。"""
    robustness: float
    """鲁棒性分。"""
    reproducibility: float
    """可复现分。"""


class StateUpdate(TypedDict, total=False):
    """:meth:`State.bump` 可更新字段全集：键确定，取代 ``**changes: Any``。"""

    session_id: str
    """所属会话 ID。"""
    run_id: str
    """所属运行 ID。"""
    rev: int
    """状态修订号（单调，用于 checkpoint 版本链）。"""
    seq: int
    """全局事件序号水位。"""
    phase: SessionState
    """会话阶段（决定主循环是否继续）。"""
    iteration: int
    """已执行的 super-step 数。"""
    repair_count: int
    """REPAIR 回边累计次数（受 MAX_REPAIR 约束）。"""
    clarify_count: int
    """ASK_USER 澄清累计次数（受 MAX_CLARIFY 约束）。"""
    spec: SpecState
    """规格槽位：模型可信结论的唯一写入位。"""
    diff_ref: str | None
    """差异产物引用（无差异时为 None）。"""
    excluded_approaches: list[str]
    """已被否决的方案列表，防止重试重复旧路。"""
    budget_used: BudgetUsage
    """四维预算累计用量。"""
    tier_history: list[str]
    """模型档位切换历史。"""
    last_error_fingerprint: str | None
    """最近一次错误指纹（用于去重/幂等，无错误时为 None）。"""
    clarification_answer: str | None
    """用户回填的澄清答案（未回填时为 None）。"""
    terminal: Terminal | None
    """终态信息（未终结时为 None）。"""


class StateSnapshot(TypedDict):
    """可断言快照的 wire 形态，键确定（用于 hash 链与 T1/T20/T26 快照断言）。"""

    session_id: str
    """所属会话 ID。"""
    run_id: str
    """所属运行 ID。"""
    rev: int
    """状态修订号。"""
    seq: int
    """全局事件序号水位。"""
    phase: SessionState
    """会话阶段。"""
    iteration: int
    """已执行的 super-step 数。"""
    repair_count: int
    """REPAIR 回边累计次数。"""
    clarify_count: int
    """ASK_USER 澄清累计次数。"""
    spec: JsonObject
    """SpecState 经 model_dump 的纯 dict（快照须为可断言、可序列化的 wire 数据）。"""
    diff_ref: str | None
    """差异产物引用。"""
    excluded_approaches: list[str]
    """已被否决的方案列表。"""
    budget_used: BudgetUsage
    """四维预算累计用量。"""
    tier_history: list[str]
    """模型档位切换历史。"""
    last_error_fingerprint: str | None
    """最近一次错误指纹。"""
    clarification_answer: str | None
    """用户回填的澄清答案。"""
    terminal: JsonObject | None
    """终态信息的 JSON 序列化形态（未终结时为 None）。"""


# ---------------------------------------------------------------------------
# 决策与状态（§5.1 / §6.2，逐字对齐）
# ---------------------------------------------------------------------------

class DecisionKind(StrEnum):
    """decide() 九种判定分支（v1.3 新增 ASK_USER / RESUME_FROM_CLARIFICATION）。

    规则 5：有限取值集合用枚举；值与 §11.3 wire 协议字符串一致，StrEnum 可直接与
    str 比较、JSON 序列化仍为字符串，故 ``d.kind == "FAILED"`` 断言保持成立。
    """

    CALL_MODEL = "CALL_MODEL"
    CALL_TOOL = "CALL_TOOL"
    REPAIR = "REPAIR"
    AWAIT_INPUT = "AWAIT_INPUT"
    DONE = "DONE"
    HANDOFF = "HANDOFF"
    FAILED = "FAILED"
    ASK_USER = "ASK_USER"
    RESUME_FROM_CLARIFICATION = "RESUME_FROM_CLARIFICATION"

    @classmethod
    def coerce(cls, raw: str) -> DecisionKind:
        """把模型/外部写入的信号归一为枚举；未知值显式回退到 CALL_MODEL（保持默认调模型的 fall-through 语义）。"""
        try:
            return cls(raw)
        except ValueError:
            return cls.CALL_MODEL


class TerminalKind(StrEnum):
    """三种终态类型（DONE 带产物；FAILED/HANDOFF 带原因码与证据）。"""

    DONE = "DONE"
    HANDOFF = "HANDOFF"
    FAILED = "FAILED"

    @classmethod
    def coerce(cls, raw: str) -> TerminalKind:
        """归一终态类型；未知值显式回退到 FAILED（安全侧，避免误判成功）。"""
        try:
            return cls(raw)
        except ValueError:
            return cls.FAILED


class CheckpointNode(StrEnum):
    """checkpoint 落盘节点（规则 5：有限取值集合用枚举）。

    取值 = decide() 的 9 种判定分支（哪个决策触发了落盘）+ FINAL（收尾补写）。
    wire 值与 DecisionKind 逐字一致，故 ``node == "CALL_MODEL"`` 断言成立。
    """

    CALL_MODEL = "CALL_MODEL"
    CALL_TOOL = "CALL_TOOL"
    REPAIR = "REPAIR"
    AWAIT_INPUT = "AWAIT_INPUT"
    DONE = "DONE"
    HANDOFF = "HANDOFF"
    FAILED = "FAILED"
    ASK_USER = "ASK_USER"
    RESUME_FROM_CLARIFICATION = "RESUME_FROM_CLARIFICATION"
    FINAL = "final"
    """循环因 MAX_ITER 退出且无终态时的收尾补写节点。"""

    @classmethod
    def from_decision(cls, kind: DecisionKind) -> CheckpointNode:
        """由判定分支转换（两者值逐字一致）；未知判定显式回退到 FINAL。"""
        try:
            return cls(kind.value)
        except ValueError:
            return cls.FINAL


class Decision(NamedTuple):
    """§11.3 逐字：纯函数 decide() 的输出，9 种 kind（v1.3 新增 2 种）。"""

    kind: DecisionKind
    """判定分支。"""
    messages: list[Message] = []  # noqa: RUF012 - §11.3 逐字默认值，NamedTuple 不可变故共享安全
    """仅在 CALL_MODEL / ASK_USER 分支携带的消息。"""
    tier: ModelTier = ModelTier.STANDARD
    """CALL_MODEL 使用的模型档位。"""
    tool: str | None = None
    """CALL_TOOL 的工具名。"""
    args: JsonObject = {}  # noqa: RUF012 - 同上，逐字语义；工具参数结构不确定，用 JsonObject 聚集
    """CALL_TOOL 的工具参数（不确定结构统一聚集）。"""
    surface: str = "generation"
    """工具调用表面。"""
    errors: list[RepairIssue] = []  # noqa: RUF012 - 同上，逐字语义
    """REPAIR 分支携带的结构化错误（字符串或 {field,expected,got} 字典）。"""
    reason_code: str | None = None
    """伴随决策的 ReasonCode。"""


@dataclass(frozen=True)
class Terminal:
    """§5.1【推导·需评审】已固化：DONE 必带 bundle_ref；FAILED/HANDOFF 必带 reason_code + evidence_ref。"""

    kind: TerminalKind
    """终态类型（规则 5：有限取值集合用枚举）。"""
    reason_code: str | None = None
    """FAILED / HANDOFF 的失败原因码。"""
    bundle_ref: str | None = None
    """DONE 的产物引用（对象存储 ref）。"""
    evidence_ref: str | None = None
    """FAILED / HANDOFF 的证据引用。"""
    finished_at: str = ""
    """ISO-8601 完成时间，由调用方注入时钟。"""

    def __post_init__(self) -> None:
        """终态不变量校验：DONE 必有产物，失败态必有原因码与证据。"""
        if self.kind == TerminalKind.DONE and not self.bundle_ref:
            raise ValueError("DONE terminal must carry bundle_ref")
        if self.kind in (TerminalKind.FAILED, TerminalKind.HANDOFF) and not (self.reason_code and self.evidence_ref):
            raise ValueError(f"{self.kind} terminal must carry reason_code and evidence_ref")


@dataclass
class State:
    """§11.3 逐字：运行时状态。字段顺序与文档一致，不得新增业务字段。"""

    session_id: str
    """会话标识。"""
    run_id: str
    """运行标识（主键 part）。"""
    rev: int
    """checkpoint 版本号，单调递增。"""
    seq: int
    """事件序号，每会话单调。"""
    phase: SessionState
    """会话状态（契约枚举）。"""
    iteration: int
    """已执行 super-step 轮次。"""
    repair_count: int
    """已发生 REPAIR 回边次数。"""
    clarify_count: int
    """已发生 ASK_USER 次数。"""
    spec: SpecState
    """会话规格与可信结论槽位（原动态键 dict，现已类型化）。"""
    diff_ref: str | None
    """规格差异引用。"""
    excluded_approaches: list[str]
    """已排除方案（压缩时必须保留，X2 不变量）。"""
    budget_used: BudgetUsage
    """四维预算用量（确定键）。"""
    tier_history: list[str]
    """每步模型档位历史。"""
    last_error_fingerprint: str | None
    """最近一次结构化错误的指纹。"""
    clarification_answer: str | None
    """用户澄清答案回注槽位。"""
    terminal: Terminal | None
    """终态；非空即停机。"""

    @classmethod
    def initial(
        cls,
        session_id: str,
        run_id: str,
        text: str = "",
        grid: str | None = None,
    ) -> State:
        """【推导·需评审】§3.3 补全：从用户输入初始化。grid 可空（v1.3 D-6 不推断）。"""
        return cls(
            session_id=session_id,
            run_id=run_id,
            rev=0,
            seq=0,
            phase=SessionState.RUNNING,
            iteration=0,
            repair_count=0,
            clarify_count=0,
            spec=SpecState(intent=text, grid=grid),
            diff_ref=None,
            excluded_approaches=[],
            budget_used={"tokens": 0, "iterations": 0, "tool_calls": 0, "sandbox_minutes": 0.0},
            tier_history=[],
            last_error_fingerprint=None,
            clarification_answer=None,
            terminal=None,
        )

    def pause(self, reason: str | None = None) -> State:
        """AWAIT_INPUT / ASK_USER → AWAITING_INPUT，挂起不占计算资源。

        reason 仅作调用方语义标注（ReasonCode 由 Decision 承载），不改状态字段。
        """
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
        # 仅终态判定能写 Terminal；其余（防御性调用）显式回退到 FAILED
        terminal_kinds = (DecisionKind.DONE, DecisionKind.HANDOFF, DecisionKind.FAILED)
        kind = (TerminalKind.coerce(decision.kind.value)
                if decision.kind in terminal_kinds else TerminalKind.FAILED)
        terminal = Terminal(
            kind=kind,
            reason_code=decision.reason_code,
            bundle_ref=bundle_ref,
            evidence_ref=evidence_ref,
            finished_at=ts,
        )
        phase = SessionState.COMPLETED if kind == TerminalKind.DONE else SessionState.FAILED
        return replace(self, phase=phase, terminal=terminal)

    def bump(self, **changes: Unpack[StateUpdate]) -> State:
        """生成一个新 State 副本（不可变式更新），供各层 apply_* 使用。

        可更新字段由 :class:`StateUpdate` 显式列出，取代旧的 ``**changes: Any``，
        既保留函数式更新语义，又让键集合在类型层面确定。
        """
        return replace(self, **changes)

    def snapshot(self) -> StateSnapshot:
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
            "spec": self.spec.model_dump(),
            "diff_ref": self.diff_ref,
            "excluded_approaches": list(self.excluded_approaches),
            "budget_used": cast(BudgetUsage, dict(self.budget_used)),
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

    @classmethod
    def from_snapshot(cls, data: StateSnapshot) -> State:
        """从 snapshot() 产出的纯 dict 还原 State（跨天恢复的反序列化边界）。

        与 :meth:`snapshot` 互逆；生产适配器从对象存储读回 JSON 后走此入口
        （M04 §3.4 checkpoint 恢复 step 3）。动态 JSON 在此一处收敛为具名视图
        （编码规则 6），下游只访问 State 的具名字段。
        """
        raw = data
        # spec 由 SpecState 严格校验（Pydantic，额外字段拒绝）
        spec = SpecState.model_validate(raw["spec"])
        # terminal 为可选嵌套结构，按 TerminalKind 枚举还原（未知值显式回退）
        terminal: Terminal | None = None
        term_raw = raw.get("terminal")
        if term_raw is not None:
            # JsonValue 是宽泛联合；terminal 各字段在 snapshot() 中均为 str|None 写入，
            # 此处显式收窄回 str|None（规则 6：动态边界单一处归一化）
            kind = TerminalKind.coerce(_json_str(term_raw.get("kind")) or "")
            terminal = Terminal(
                kind=kind,
                reason_code=_json_str(term_raw.get("reason_code")),
                bundle_ref=_json_str(term_raw.get("bundle_ref")),
                evidence_ref=_json_str(term_raw.get("evidence_ref")),
                finished_at=_json_str(term_raw.get("finished_at")) or "",
            )
        return cls(
            session_id=str(raw["session_id"]),
            run_id=str(raw["run_id"]),
            rev=int(raw["rev"]),
            seq=int(raw["seq"]),
            phase=SessionState(str(raw["phase"])),
            iteration=int(raw["iteration"]),
            repair_count=int(raw["repair_count"]),
            clarify_count=int(raw["clarify_count"]),
            spec=spec,
            diff_ref=raw.get("diff_ref"),
            excluded_approaches=list(raw.get("excluded_approaches") or []),
            budget_used=cast(BudgetUsage, dict(raw.get("budget_used") or {})),
            tier_history=list(raw.get("tier_history") or []),
            last_error_fingerprint=raw.get("last_error_fingerprint"),
            clarification_answer=raw.get("clarification_answer"),
            terminal=terminal,
        )


# ---------------------------------------------------------------------------
# 消息与模型（§4.1 L1/L2）
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """一条对话消息（role + 文本内容）。"""

    role: str
    """角色（system / user / assistant / tool）。"""
    content: str
    """消息正文。"""

    def to_dict(self) -> dict[str, str]:
        """转成 wire 形态（字符串键、字符串值）。"""
        return {"role": self.role, "content": self.content}


@dataclass
class Input:
    """loop.run 入参（§11.3）。text 即归一化前描述；grid 可空（v1.3 D-6）。"""

    text: str = ""
    """用户输入描述。"""
    grid: str | None = None
    """用户选择的规格；可空。"""
    attachments: list[str] = field(default_factory=list)
    """附件引用列表。"""


@dataclass
class Usage:
    """usage 归一结果（跨供应商字段不同，统一到此）。"""

    input_tokens: int = 0
    """输入 token 数。"""
    output_tokens: int = 0
    """输出 token 数。"""

    @property
    def total_tokens(self) -> int:
        """总 token 数 = 输入 + 输出。"""
        return self.input_tokens + self.output_tokens


@dataclass
class ModelOutput:
    """model.complete 返回（含可控半截 JSON / 越权代码用于故障注入）。"""

    content: str = ""
    """模型文本输出。"""
    usage: Usage = field(default_factory=Usage)
    """归一后的用量。"""
    finish_reason: str = "stop"
    """供应商结束原因。"""
    tier: ModelTier = ModelTier.STANDARD
    """本次调用的模型档位。"""
    raw: JsonObject = field(default_factory=dict)
    """供应商原始响应（不确定结构，统一聚集到 JsonObject）。"""


@dataclass
class Delta:
    """stream.parse 输出的单个增量。"""

    text: str = ""
    """增量文本。"""
    done: bool = False
    """是否为 [DONE] 结束标记。"""


@dataclass
class StreamBuffer:
    """SSE 半帧缓存。"""

    text: str = ""
    """尚未凑齐 ``\\n\\n`` 的半帧文本。"""
    deltas: list[Delta] = field(default_factory=list)
    """已解析出的增量累积。"""


# ---------------------------------------------------------------------------
# 结构化输出（§4.1 L5/L6/L7）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairError:
    """§3.4 D【推导·需评审】固化：{field, expected, got}。"""

    field: str
    """出错字段路径。"""
    expected: str
    """期望值描述。"""
    got: str
    """实际值描述（截断）。"""

    def as_dict(self) -> dict[str, str]:
        """转成 {field, expected, got} 字典（写回 spec.errors）。"""
        return {"field": self.field, "expected": self.expected, "got": self.got}


@dataclass
class ValidationResult:
    """final 严格校验结果。"""

    ok: bool
    """是否通过校验。"""
    data: JsonObject | None = None
    """通过时的解析数据（不确定结构，用 JsonObject）。"""
    errors: list[RepairError] = field(default_factory=list)
    """失败时的结构化错误。"""
    code: str | None = None
    """失败原因码（如 CONTRACT_MISMATCH）。"""


@dataclass
class PartialObject:
    """partial.parse 结果：complete=False 表示 JSON 未闭合（流中）。"""

    value: JsonObject = field(default_factory=dict)
    """尽力解析出的对象（不确定结构，用 JsonObject）。"""
    complete: bool = False
    """原始文本是否已完整闭合。"""
    raw: str = ""
    """原始输入文本。"""


# ---------------------------------------------------------------------------
# 工具（§4.1 L8/L9/L10，§6.2 ToolSpec 逐字）
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """一个已注册工具的静态描述。"""

    name: str
    """工具唯一名。"""
    args_model: type[BaseModel]
    """参数校验模型（Pydantic）。"""
    surfaces: set[str]
    """允许调用的表面集合。"""
    # 【已登记的框架边界豁免】handler 由外部 args_model 决定，返回类型无法静态收窄。
    handler: Callable[..., object]
    """执行函数；参数由 args_model 校验后传入，返回值不确定故用 object。"""
    max_output_chars: int
    """输出截断上限。"""


@dataclass
class ToolResult:
    """§6.2【推导·需评审】固化：output 已截断。"""

    ok: bool
    """是否成功。"""
    output: str
    """已截断的输出文本。"""
    truncated: bool = False
    """是否发生过截断。"""
    error_code: str | None = None
    """失败原因码。"""


# ---------------------------------------------------------------------------
# 护栏（§4.1 L24/L25/L26）
# ---------------------------------------------------------------------------

class _GuardName(StrEnum):
    """护栏名（规则 5：跨模块对齐的私有枚举，值与 wire 协议一致）。"""

    INPUT = "input_guard"
    OUTPUT = "output_guard"


class _HitType(StrEnum):
    """命中类型（规则 5：护栏内置的有限命中类型；规则自定义类型仍用 str 透传）。"""

    PROMPT_INJECTION = "prompt_injection"
    PROMPT_LEAK = "prompt_leak"
    PII = "pii"
    OUT_OF_SCOPE = "out_of_scope"
    FORBIDDEN_EVAL = "forbidden_eval"
    FORBIDDEN_NETWORK = "forbidden_network"
    UNKNOWN_IMPORT = "unknown_import"


@dataclass
class GuardVerdict:
    """护栏判定结果（命中时携带留证）。"""

    hit: bool
    """是否命中。"""
    guard: str = ""
    """命中的护栏名（input_guard / output_guard）。"""
    hit_type: str | None = None
    """命中类型（prompt_injection / pii / ...）。"""
    criterion: str | None = None
    """判据（规则 ID）。"""
    source_ref: str | None = None
    """原文引用（命中片段）。"""
    reason_code: str | None = None
    """命中对应的 ReasonCode。"""


@dataclass(frozen=True)
class GuardHit:
    """guard_hit 表的一条留证（类型 / 判据 / 原文引用）。"""

    guard: str
    """护栏名。"""
    hit_type: str
    """命中类型。"""
    criterion: str
    """判据。"""
    source_ref: str
    """原文引用。"""
    session_id: str = ""
    """会话标识。"""
    run_id: str = ""
    """运行标识。"""
    seq: int = 0
    """事件序号。"""


@dataclass
class CapabilityCall:
    """一次能力调用请求。"""

    name: str
    """能力名（须在 canonical 白名单内）。"""
    surface: str = "generation"
    """调用表面。"""


@dataclass
class Manifest:
    """WidgetBundle.manifest 关键字段的能力比对子集（§6.2）。"""

    capabilities: set[str] = field(default_factory=set)
    """已声明能力集合。"""
    import_allowlist: tuple[str, ...] = ("react", "@widget/bridge")
    """允许的 import 白名单。"""
    exports_default: str = "Widget"
    """默认导出名。"""
    spec_version: str = "1.0"
    """规格版本。"""
    retrieval_snapshot: str = ""
    """检索快照标识。"""
    model: str = ""
    """模型标识。"""
    model_tier: str = "standard"
    """模型档位。"""
    prompt_version: str = ""
    """prompt 版本。"""
    recipe_version: str = ""
    """配方版本。"""


# ---------------------------------------------------------------------------
# 事件（§4.2 AgentEvent 逐字）
# ---------------------------------------------------------------------------

@dataclass
class AgentEvent:
    """§4.2 事件记录：kind 属于 AgentEventKind（18 种之一）。"""

    seq: int
    """会话内递增序号。"""
    session_id: str
    """会话标识。"""
    run_id: str
    """运行标识。"""
    ts: str
    """ISO-8601 时间戳。"""
    kind: str
    """事件类型（AgentEventKind 值）。"""
    phase: str
    """发生时的会话状态。"""
    payload: JsonValue
    """事件载荷（结构随 kind 变化，统一用 JsonValue 聚集）。"""
    trace_id: str
    """链路追踪 ID。"""

    def to_dict(self) -> JsonObject:
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
    """问题标识。"""
    question: str
    """问题文本。"""
    options: list[str] = field(default_factory=list)
    """封闭式选项（≤2 次/任务）。"""
    allow_text: bool = True
    """是否允许自由文本回答。"""


# ---------------------------------------------------------------------------
# 上下文 / 预算 / 评测（§4.1 L11/L12/L13/L27-L29）
# ---------------------------------------------------------------------------

@dataclass
class SlotConfig:
    """固定槽位装配配置；顺序固定保住 prompt caching。"""

    include_memory: bool = True
    """是否装配用户记忆槽。"""
    include_retrieval: bool = True
    """是否装配检索片段槽。"""
    max_tokens: int = 8000
    """槽位 token 预算。"""
    now: datetime | None = None
    """注入时钟（不读系统时间）。"""


@dataclass
class Candidate:
    """检索候选（向量 / BM25 结果统一形态）。"""

    doc_id: str
    """文档标识。"""
    text: str
    """候选文本。"""
    score: float
    """检索得分。"""
    source: str = "vector"
    """来源（vector / bm25）。"""


@dataclass
class Trajectory:
    """eval/replay 输出：可断言的确定性轨迹（T26 快照相等）。"""

    events: list[JsonObject] = field(default_factory=list)
    """序列化后的事件序列（不确定结构，用 JsonObject）。"""
    terminal: JsonObject | None = None
    """终态快照（不确定结构，用 JsonObject）。"""
    # dict() 会被推断为 dict[Unknown, Unknown]，cast 收口到确定键的 TypedDict（规则 3）
    fingerprint: FingerprintInfo = field(default_factory=lambda: cast(FingerprintInfo, {}))
    """复现指纹五要素（确定键）。"""


@dataclass
class EvalCase:
    """一条评测用例。"""

    case_id: str
    """用例标识。"""
    input: JsonObject = field(default_factory=dict)
    """用例输入（不确定结构，用 JsonObject）。"""
    expected: JsonObject = field(default_factory=dict)
    """期望断言（不确定结构，用 JsonObject）。"""
    fingerprint: FingerprintInfo = field(default_factory=lambda: cast(FingerprintInfo, {}))
    """复现指纹五要素。"""


@dataclass
class Score:
    """一条轨迹的七维评分。"""

    case_id: str
    """对应用例标识。"""
    dimensions: ScoreDimensions = field(default_factory=lambda: cast(ScoreDimensions, {}))
    """七维度得分（确定键）。"""
    passed: bool = False
    """是否通过门禁。"""

    @property
    def total(self) -> float:
        """七维度加权平均（归一化到 [0,1]）。"""
        if not self.dimensions:
            return 0.0
        # TypedDict.values() 被推断为 object，cast 到 float 序列再做数值求和
        return sum(cast(float, v) for v in self.dimensions.values()) / len(self.dimensions)


@dataclass
class GateResult:
    """CI 门禁判定结果。"""

    ok: bool
    """是否通过。"""
    failures: list[str] = field(default_factory=list)
    """失败原因列表。"""
    detail: str = ""
    """详情文本。"""

    @property
    def label(self) -> str:
        """可读标签（GATE OK / GATE FAIL）。"""
        return "GATE OK" if self.ok else "GATE FAIL"
