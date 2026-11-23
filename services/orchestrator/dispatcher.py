"""M04 编排回路 dispatcher（B1b + review B1/B2/B3/B6 修复）。

把「提交描述 → 入队 → 消费 → agent loop → 终态回写」接成生产可运行链路：
- RedisTaskQueue：Redis Streams 队列（consumer group + 成功后 ACK + PEL 重投）；
- Dispatcher.process_one：取任务跑 M03 run()，终态映射回 SessionService。

关键不变量（review 修复）：
- 任务携带 user_id，挂起回写用真实 owner（不再传空，避免 ForbiddenError）；
- 仅 run()+回写成功才 ACK；失败留 PEL，由 xautoclaim 重投，超上限写死信+
  fail 落终态（不静默丢任务）；
- 恢复任务由 worker 从 sessions 表按 run_id 反查完整 session_id/user_id 后入队；
- DONE 无 bundle_ref 转 FAILED（M08 未接入前不假绿）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Final, cast

import redis

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.tools.registry import level0
from packages.agent_core.types import Input, JsonObject, State, TerminalKind
from packages.contracts.enums import ReasonCode, SessionState
from packages.contracts.types import TaskFields
from services.control_plane.sessions.service import SessionService
from services.guardrails.types import ValidationResult
from services.orchestrator.memory.fact_summary import (
    build_fact_summary,
    render_fact_summary,
)
from services.orchestrator.memory.ports import FactSummaryStorePort
from services.orchestrator.tasks.ports import (
    PlanLLM,
    TaskStorePort,
    TaskTurnStorePort,
)
from services.orchestrator.tasks.states import TaskState

logger = logging.getLogger(__name__)
"""dispatcher 模块日志器（事实摘要写失败只告警不阻断主流程）。"""


def _l0_catalog() -> tuple[JsonObject, ...]:
    """从启动期冻结的 registry 取 L0 目录（ADR-003 / B1）。"""
    return tuple(level0())


# A1（v1.8 接线）：校验链可注入入口。签名只取生产可提供的入参，
# 复杂端口（embedder/store 等）由装配层绑定成闭包，dispatcher 不感知（关注点分离）。
ValidatorFn = Callable[[str, TaskFields], ValidationResult]
# 校验「硬拒绝」原因码（不放行进模型且转 FAILED）：输入护栏命中 / 越界 / 预算 / 契约不符。
# 其余 ok=False（规格缺失/冲突、role 歧义 INPUT_INVALID、澄清请求）按语义挂起转 AWAITING_INPUT，
# 对齐 M06 §3.2「护栏拒绝 vs 语义补充」两类去向，也对齐 §3.3 状态机。
_REJECT_CODES: Final[frozenset[ReasonCode]] = frozenset(
    {
        ReasonCode.GUARD_INPUT_HIT,
        ReasonCode.INTENT_OUT_OF_SCOPE,
        ReasonCode.BUDGET_EXHAUSTED,
        ReasonCode.CONTRACT_MISMATCH,
    }
)

_TASK_FIELDS: Final[str] = "data"
_GROUP: Final[str] = "orchestrator"
_CONSUMER: Final[str] = "worker-1"
_FIELD_SESSION: Final[str] = "session_id"
_FIELD_RUN: Final[str] = "run_id"
_FIELD_DESC: Final[str] = "description"
_FIELD_GRID: Final[str] = "grid"
_FIELD_ATTACHMENTS: Final[str] = "attachment_ids"
_FIELD_CLARIFIED_GRID: Final[str] = "clarified_grid"
_FIELD_RESUME: Final[str] = "resume"
_FIELD_CLARIFICATION_REPLY: Final[str] = "clarification_reply"
_FIELD_USER: Final[str] = "user_id"
_FIELD_PLANNING: Final[str] = "planning"
"""ADR-006：planning=1 表示规划任务（先 V1 门禁再规划，非普通生成任务）。"""
_FIELD_TASK_ID: Final[str] = "task_id"
_FIELD_PARENT_RUN: Final[str] = "parent_run_id"
_FIELD_TASK_INDEX: Final[str] = "task_index"
_FIELD_TASK_TOTAL: Final[str] = "task_total"
_FIELD_TARGET_TASK: Final[str] = "target_task_id"
_FIELD_RETRIES: Final[str] = "retries"
"""重试计数（无下划线前缀 = 随 Stream 持久化，跨 XAUTOCLAIM 往返保持；review 复审 B2）。"""
_FIELD_MSG_ID: Final[str] = "_msg_id"
"""Stream 消息 id（下划线前缀 = 仅内存，ACK 用，不入 wire）。"""
_EVIDENCE_PREFIX: Final[str] = "dispatcher"
_DEFAULT_BLOCK_MS: Final[int] = 1000
_MAX_RETRY: Final[int] = 3
_CLAIM_IDLE_MS: Final[int] = 5000
_DEAD_LETTER_SUFFIX: Final[str] = ":dead"
TASK_QUEUE_KEY: Final[str] = "orchestrator:tasks"
"""生产任务队列 Stream key（control-plane 入队 / orchestrator 消费共用）。"""
_NO_BUNDLE_REASON: Final[str] = "NO_VERIFIED_BUNDLE"
"""DONE 无可信产物时的失败原因码（M08 未接入前不假绿）。"""


type TaskValue = str | list[str] | None
"""任务字段值：wire 上是 JSON 对象，附件 id 列表与可空规格都是合法形态。"""

type Task = dict[str, TaskValue]
"""任务载荷：session_id/user_id/run_id/description/grid + 内部重试/消息 id。"""


def _as_str(value: bytes | str | None) -> str:
    """把 redis-py 的 bytes/str 统一解码为 str（wire 契约用字符串）。

    Args:
        value: Redis 返回的 bytes 或 str 值（缺键时为 None）。
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value if value is not None else ""


def _task_str(value: TaskValue) -> str:
    """把任务字段值收窄为 str（任务 JSON 边界仍可能含 list，沿用旧 str() 行为）。"""
    return str(value) if value is not None else ""


def _task_attachments(task: Task) -> list[str]:
    """从任务载荷收窄附件 id 列表（B2：attachment_refs 透传给 run Input）。

    畸形标量输入按单元素列表包裹，保持与 _check_guardrail 旧归一逻辑等价。
    """
    raw = task.get(_FIELD_ATTACHMENTS, [])
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return [str(raw)] if raw else []


def _read_fact_summary_with_index(
    store: FactSummaryStorePort | None, session_id: str
) -> tuple[int, str]:
    """ADR-009：读会话事实（turn_index + 文本）；读失败返回 (0, "") 容错。

    事实是建议不是门禁：PG 读故障只省略该段，不冒泡成任务失败
    （否则走不 ACK→重投→死信，违反契约 Invariant「读取失败容错」）。
    """
    if store is None:
        return 0, ""
    try:
        return store.latest(session_id)
    except Exception as exc:  # noqa: BLE001 - 事实读取失败只省略，不影响主生成链
        logger.warning("fact summary read failed; omit segment: %s", exc)
        return 0, ""


def _read_fact_summary(
    store: FactSummaryStorePort | None, session_id: str
) -> str:
    """只取渲染文本（装配用）；读失败为空串。"""
    return _read_fact_summary_with_index(store, session_id)[1]


def _decode_message(msg_id: str | bytes, fields: dict[bytes | str, bytes | str]) -> Task:
    """把一条 Stream 消息解码为 Task（兼容 bytes 键，附内部消息 id）。

    Args:
        msg_id: Stream 消息 id。
        fields: XREADGROUP/XAUTOCLAIM 返回的消息字段映射。

    Returns:
        含 _msg_id 的任务字典。
    """
    raw = next(
        (v for k, v in fields.items() if _as_str(k) == _TASK_FIELDS),
        "{}",
    )
    task: Task = json.loads(_as_str(raw))
    task[_FIELD_MSG_ID] = _as_str(msg_id)
    return task


class RedisTaskQueue:
    """Redis Streams 任务队列：XADD 入队 + XREADGROUP/XAUTOCLAIM 消费 + 成功 ACK。"""

    def __init__(
        self,
        redis_url: str,
        key: str,
        consumer: str = _CONSUMER,
    ) -> None:
        """注入 Redis URL、Stream key 与消费者标识。

        Args:
            redis_url: Redis 连接串。
            key: Stream 键名（按部署隔离）。
            consumer: consumer group 内的消费者名（多 worker 各不同）。
        """
        self._redis_url = redis_url
        self._key = key
        self._consumer = consumer
        self._client: redis.Redis | None = None

    def _conn(self) -> redis.Redis:
        """惰性创建同步 Redis 客户端。"""
        if self._client is None:
            self._client = redis.from_url(self._redis_url)
        return self._client

    def _ensure_group(self, client: redis.Redis) -> None:
        """确保 consumer group 存在（首次消费时建，已存在则忽略）。"""
        try:
            client.xgroup_create(self._key, _GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(self, task: Task) -> str:
        """把任务写入 Stream（XADD）；清除上次投递的内部字段。

        Args:
            task: 任务载荷（session_id/user_id/run_id/description/grid）。

        Returns:
            Stream 消息 id。
        """
        wire = {k: v for k, v in task.items() if not k.startswith("_")}
        return str(self._conn().xadd(self._key, {_TASK_FIELDS: json.dumps(wire)}))

    def consume(self, batch: int = 1) -> list[Task]:
        """先重投本消费者超时 PEL，再读新消息（至少一次投递）。

        Args:
            batch: 单次最多读取条数。

        Returns:
            任务载荷列表；无待处理任务返回空列表。
        """
        client = self._conn()
        self._ensure_group(client)
        reclaimed = self._reclaim(client, batch)
        if reclaimed:
            return reclaimed
        streams = client.xreadgroup(
            _GROUP,
            self._consumer,
            {self._key: ">"},
            count=batch,
            block=_DEFAULT_BLOCK_MS,
        )
        if not streams:
            return []
        tasks: list[Task] = []
        # redis-py 类型桩把 xreadgroup 的返回收窄成 int，这里按 wire 契约显式断言：
        # [(stream, [(msg_id, fields), ...])]，字段再由 _decode_message 解码。
        groups = cast(
            list[
                tuple[
                    bytes | str,
                    list[tuple[str | bytes, dict[bytes | str, bytes | str]]],
                ]
            ],
            streams,
        )
        for _stream, messages in groups:
            for msg_id, fields in messages:
                tasks.append(_decode_message(msg_id, fields))
        return tasks

    def _reclaim(self, client: redis.Redis, batch: int) -> list[Task]:
        """XAUTOCLAIM 认领超时 PEL，带递增计数重新入队后 ACK 旧消息。

        Stream 消息不可变，重试计数必须随「新入队消息」持久化才能跨轮次累计
        （复审 B2：仅在内存 task 上递增会每轮从 0 重置，死信永不触发）。
        重新入队后返回空列表，由 consume 的正常 ``>`` 路径读到新消息。

        Args:
            client: Redis 客户端。
            batch: 最多认领条数。

        Returns:
            永远返回空列表（重投的消息已重新入队，走正常读取）。
        """
        try:
            _next, claimed, _deleted = client.xautoclaim(
                self._key,
                _GROUP,
                self._consumer,
                min_idle_time=_CLAIM_IDLE_MS,
                start_id="0-0",
                count=batch,
            )
        except redis.ResponseError:
            return []  # 老版本 Redis 无 XAUTOCLAIM：退化为只读新消息
        for msg_id, fields in claimed:
            task = _decode_message(msg_id, fields)
            retries = int(_task_str(task.get(_FIELD_RETRIES, "0")) or "0") + 1
            task[_FIELD_RETRIES] = str(retries)
            task.pop(_FIELD_MSG_ID, None)  # 新消息会分配新 msg_id
            self.enqueue(task)
            self._conn().xack(self._key, _GROUP, msg_id)  # ACK 旧消息，PEL 不残留
        return []

    def ack(self, task: Task) -> None:
        """确认任务成功处理（XACK）。

        Args:
            task: consume 返回的任务（含内部 _msg_id）。
        """
        msg_id = _task_str(task.get(_FIELD_MSG_ID)) or None
        if msg_id:
            self._conn().xack(self._key, _GROUP, msg_id)

    def dead_letter(self, task: Task) -> None:
        """重试用尽后写死信 Stream 并 ACK 原消息（人工介入入口）。

        Args:
            task: 失败任务。
        """
        self._conn().xadd(
            self._key + _DEAD_LETTER_SUFFIX,
            {
                _TASK_FIELDS: json.dumps(
                    {k: v for k, v in task.items() if not k.startswith("_")}
                )
            },
        )
        self.ack(task)

    def replay_expired(self, tasks: list[Task]) -> int:
        """把恢复扫描得到的完整任务重新入队（worker 反查 session 后调用）。

        Args:
            tasks: 每条含 session_id/user_id/run_id 的完整任务。

        Returns:
            重新入队条数。
        """
        for task in tasks:
            self.enqueue(task)
        return len(tasks)


class Dispatcher:
    """消费任务并驱动 agent loop，终态回写会话状态。"""

    def __init__(
        self,
        queue: RedisTaskQueue,
        deps: Deps,
        sessions: SessionService,
        validator: ValidatorFn,
        input_enricher: Callable[[Input], Input] | None = None,
        cost_sink: Callable[[State], None] | None = None,
        fact_summary_store: FactSummaryStorePort | None = None,
        *,
        task_store: TaskStorePort | None = None,
        plan_llm: PlanLLM | None = None,
        task_turn_store: TaskTurnStorePort | None = None,
    ) -> None:
        """注入任务队列、执行依赖、会话服务与校验链（状态回写入口）。

        Args:
            queue: 任务队列（消费 + ACK + 死信）。
            deps: M03 run() 的全部依赖（生产真实 / 测试 fake）。
            sessions: 会话状态机服务（终态回写）。
            validator: M06 校验链入口（A1）；生产注入真实装配，测试注入放行桩。
            input_enricher: v1.6 上下文端口注入回调（生产由 worker 绑定真实
                Preference/Procedure 端口）；None 时 Input 不带数据端口，装配省略对应槽。
            cost_sink: B7 终态成本落账回调（生产由 worker 绑定真实 PG）；
                None 时不落账（run 的注入点，非测试兼容桩）。
            fact_summary_store: ADR-009 会话事实摘要存储（生产 PgFactSummaryStore）；
                None 时不注入/不生成事实（无存储即无事实，非测试兼容桩）。
            task_store: ADR-006 generation_task 存储（生产 PgGenerationTaskStore）；
                多任务落库与 fan-in 查询。None 时多任务路径不可用（fail-fast）。
            plan_llm: ADR-006 约束式规划器真实端口；None 时多任务规划 fail-fast。
            task_turn_store: ADR-009 task_turn 存储（生产 PgTaskTurnStore）。
        子任务直接走 run() 单 agent 内核（ADR-011：角色层已移除）。
        """
        self._queue = queue
        self._deps = deps
        self._sessions = sessions
        self._validator = validator
        self._input_enricher = input_enricher
        self._cost_sink = cost_sink
        self._fact_summary_store = fact_summary_store
        self._task_store = task_store
        self._plan_llm = plan_llm
        self._task_turn_store = task_turn_store
        # Coordinator fan-in 事件的原子 seq 分配器（由 Redis URL 构造，惰性连接）。
        from services.orchestrator.tasks.events import RedisSeqAllocator

        redis_url = getattr(queue, "_redis_url", "")
        self._seq_allocator = RedisSeqAllocator(str(redis_url)) if redis_url else None

    def _write_fact(
        self, session_id: str, state: State, last_intent: str, widget_type: str
    ) -> None:
        """ADR-009：DONE 终态生成并落一条 FactSummary（零 LLM，幂等）。"""
        if self._fact_summary_store is None:
            return
        # 读上一轮 turn 也走容错（读失败按 turn 1 尽力写，不阻断已完成的会话）
        prev_turn, _ = _read_fact_summary_with_index(self._fact_summary_store, session_id)
        fact = build_fact_summary(
            session_id, state,
            turn_index=prev_turn + 1,
            last_intent=last_intent,
            widget_type=widget_type,
        )
        self._fact_summary_store.append(
            session_id, fact.turn_index,
            {
                "session_id": fact.session_id,
                "turn_index": fact.turn_index,
                "tasks": [
                    {
                        "widget_type": t.widget_type, "grid": t.grid,
                        "palette": t.palette, "key_features": list(t.key_features),
                        "last_intent": t.last_intent, "turn_count": t.turn_count,
                    }
                    for t in fact.tasks
                ],
            },
            render_fact_summary(fact),
        )

    def _write_back(
        self,
        session_id: str,
        user_id: str,
        state: State,
        *,
        last_intent: str = "",
        widget_type: str = "custom",
    ) -> None:
        """把 run() 终态映射回 SessionService（§5.2 状态迁移集中一处）。

        Args:
            session_id: 会话标识。
            user_id: 会话 owner（租户校验用，不再传空——review B1）。
            state: run() 返回的最终 State。
            last_intent: 本轮用户描述（ADR-009 FactSummary 用）。
            widget_type: 本轮归一化控件类型（FactSummary 用）。
        """
        if state.terminal is None:
            # 无终态但进入挂起（ask_user / AWAIT_INPUT）→ 等用户输入
            if state.phase is SessionState.AWAITING_INPUT:
                self._sessions.mark_awaiting_input(
                    session_id, user_id, reason_code=state.spec.reason or "SPEC_MISSING"
                )
            return
        if state.terminal.kind is TerminalKind.DONE:
            bundle_ref = state.terminal.bundle_ref
            # 【M08 V3 强制门禁待接】当前 loop 终态只携带模型自报 bundle_ref，
            # 无经校验的 WidgetSpec + TSX source_ref + manifest（M08 validate_and_test
            # 的三个必需入参）。产物本体须由 M07 结构化生成回路落对象存储后，
            # 在此处调用 services.sandbox_runnerentry.validate_and_test 并以
            # static_check/sandbox_test 通过为完成前提；在此之前 bundle_ref 仅作
            # 引用记录，不能视为已通过出护栏。缺口已登记 M08 模块文档 §9。
            if not bundle_ref:
                # review B6：模型自报产物不可信，空引用直接失败不假绿
                self._sessions.fail(
                    session_id,
                    _NO_BUNDLE_REASON,
                    f"{_EVIDENCE_PREFIX}/{session_id}/{state.run_id}/no-bundle",
                )
            else:
                self._sessions.complete(session_id, bundle_ref)
                # ADR-009：仅成功且有产物时生成事实；失败 turn 不生成新事实。
                # 写失败只丢事实证据（store 层 fail-closed 抛异常），不回滚已完成的
                # 会话状态、不阻断 ACK——事实是建议不是门禁。
                try:
                    self._write_fact(session_id, state, last_intent, widget_type)
                except Exception as exc:  # noqa: BLE001 - 事实写失败只丢证据
                    logger.warning("fact summary write failed; continue: %s", exc)
        else:
            # FAILED / HANDOFF：原因码 + 证据引用缺一不可（A14）
            reason = state.terminal.reason_code or "HUMAN_REQUIRED"
            evidence = state.terminal.evidence_ref or (
                f"{_EVIDENCE_PREFIX}/{session_id}/{state.run_id}"
            )
            self._sessions.fail(session_id, reason, evidence)

    def _check_guardrail(
        self, session_id: str, run_id: str, user_id: str, task: Task
    ) -> ValidationResult | None:
        """A1：run() 前过 M06 校验链；返回放行结论或 None（未校验/恢复跳过）。

        把任务字段收敛成一个 dict 传给装配层绑定的 validator 闭包——dispatcher
        不感知 embedder/store 等重依赖（由 worker 装配时绑定）。命中/挂起在此
        直接回写会话状态并返回 ``ok=False`` 的结论（调用方跳过 run()）；
        放行返回 ``ok=True`` 结论（B2：供 process_one 把全字段传进 run()）；
        恢复任务或未注入 validator 返回 None。

        Args:
            session_id: 会话标识。
            run_id: 执行标识。
            user_id: 会话 owner（回写租户校验用）。
            task: 原始任务字段（校验入参来源）。

        Returns:
            ``None`` = 未校验/恢复跳过（直接 run）；``ok=False`` = 已回写终态须跳过；
            ``ok=True`` = 放行，携带 M06 全字段供下游消费。

        Raises:
            SessionError / ConflictError: 状态回写冲突（与 _write_back 同口径）。
        """
        # M5（v1.8）：恢复任务（worker 从过期租约扫描重建，description 为空）跳过
        # 校验链——首次提交已过护栏，恢复是 checkpoint 续跑而非新请求，
        # 否则空描述会被判 INPUT_INVALID 误挂起，跨实例恢复能力失效。
        if task.get(_FIELD_RESUME) == "1":
            return None
        # 任务字段来自 JSON 边界，逐项归一到 TaskFields 的确定形状；
        # attachment_ids 兼容畸形标量输入（旧装配层也做 list 包裹），保持行为等价。
        attachment_ids = _task_attachments(task)
        fields: TaskFields = {
            "session_id": session_id,
            "run_id": run_id,
            "description": _task_str(task.get(_FIELD_DESC, "")),
            "grid": _task_str(task.get(_FIELD_GRID)) or None,
            "attachment_ids": attachment_ids,
            "clarified_grid": _task_str(task.get(_FIELD_CLARIFIED_GRID)),
            "clarification_reply": _task_str(
                task.get(_FIELD_CLARIFICATION_REPLY, "")
            ),
            "user_id": user_id,  # B1：偏好读取租户键
        }
        result = self._validator(str(task.get(_FIELD_DESC, "")), fields)
        if result.ok:
            return result  # B2：放行结论全字段交给 process_one 消费
        ref = result.evidence_ref or ""
        reason = result.reason_code.value if result.reason_code else ""
        if result.reason_code in _REJECT_CODES:
            # 硬拒绝：转 FAILED，用 ValidationResult 带 hash 的证据替代自拼前缀（A4）
            self._sessions.fail(
                session_id, reason or ReasonCode.GUARD_INPUT_HIT.value, ref
            )
        else:
            # 语义挂起（规格缺失/冲突、role 歧义）：转 AWAITING_INPUT（C2/C3）
            self._sessions.mark_awaiting_input(
                session_id, user_id, reason or ReasonCode.SPEC_MISSING.value, ref
            )
        return result  # 已回写终态/挂起，调用方据此跳过 run()

    def _plan_and_fanout(
        self, session_id: str, run_id: str, user_id: str, task: Task
    ) -> bool:
        """ADR-006 V1→规划→V2→fan-out；返回 True 表示已处理（调用方跳过 run()）。

        N=1 或单 widget 信号 → 走现有 run()（返回 False，零回归）；
        N>1 → 落 generation_task + 入队子任务；歧义/校验失败 → AWAITING_INPUT。
        生产无 plan_llm/task_store 时多任务请求 fail-fast（不静默降级规则规划）。
        """
        from services.orchestrator.tasks.fanout import (
            needs_planning,
            run_fanout_planning,
        )
        from services.orchestrator.tasks.planner import extract_facts

        description = _task_str(task.get(_FIELD_DESC))
        if not needs_planning(description):
            return False  # 单 widget 信号：走现有 run() 直路（§6.7 零回归）

        if self._plan_llm is None or self._task_store is None:
            raise RuntimeError(
                "multi-widget planning requires PlanLLM + GenerationTaskStore ports "
                "(production must inject real ports; no rule-based fallback)"
            )
        facts = extract_facts(
            session_id,
            run_id,
            description,
            attachments=(),  # 附件事实由 worker 的 enrichment/port 注入后可扩展
            confirmed_grid=None,
            budget_remaining=0.0,
            max_tasks=3,
            trace_id=run_id,
        )
        from packages.contracts.enums import GridSpec

        grid_raw = _task_str(task.get(_FIELD_GRID))
        if grid_raw:
            try:
                facts = extract_facts(
                    session_id, run_id, description,
                    confirmed_grid=GridSpec(grid_raw), trace_id=run_id,
                )
            except ValueError:
                pass
        result = run_fanout_planning(facts, self._plan_llm)
        if result.errors or result.ambiguous_reason:
            reason = result.reason_code or ReasonCode.CLARIFICATION_REQUIRED.value
            # S5（review）：V2 硬拒绝码（幻觉附件 id / 注入 / 契约不符）与 V1 同口径
            # 走 fail（整份拒绝、不落 generation_task）；仅歧义/规格缺失挂起。
            hard_reject = {
                ReasonCode.CONTRACT_MISMATCH.value,
                ReasonCode.GUARD_INPUT_HIT.value,
                ReasonCode.BUDGET_EXHAUSTED.value,
            }
            if reason in hard_reject:
                self._sessions.fail(
                    session_id, reason, f"plan-v2/{session_id}/{run_id}"
                )
            else:
                self._sessions.mark_awaiting_input(session_id, user_id, reason, "")
            return True
        if len(result.tasks) == 1:
            return False  # N=1：沿用现有单任务链路，不启用角色层

        from uuid import uuid4

        from services.orchestrator.tasks.pg_stores import GenerationTaskData

        # B2（review）：每个子任务分配独立 run_id —— generation_task.run_id UNIQUE，
        # 共用父 run_id 会让第 2 个子任务撞约束；同时租约/checkpoint/cost_ledger
        # 都按 run_id 隔离，必须一任务一 run。
        child_runs: tuple[str, ...] = tuple(str(uuid4()) for _ in result.tasks)
        rows: tuple[GenerationTaskData, ...] = tuple(
            GenerationTaskData(
                id=t.task_id,
                session_id=session_id,
                parent_run_id=run_id,
                run_id=child_runs[i],
                original_description=description,
                description=t.description,
                task_index=t.task_index,
                task_total=t.task_total,
                state=TaskState.QUEUED.value,
                grid=t.grid.value if t.grid else None,
                attachment_ids=t.attachment_ids,
                plan_trace_ref=result.plan_trace_ref,
            )
            for i, t in enumerate(result.tasks)
        )
        self._task_store.create_many(rows)
        for t, child_run in zip(result.tasks, child_runs):
            self._queue.enqueue(
                {
                    _FIELD_SESSION: session_id,
                    _FIELD_USER: user_id,
                    _FIELD_RUN: child_run,
                    _FIELD_TASK_ID: t.task_id,
                    _FIELD_PARENT_RUN: run_id,
                    _FIELD_TASK_INDEX: str(t.task_index),
                    _FIELD_TASK_TOTAL: str(t.task_total),
                    _FIELD_DESC: t.description,
                    _FIELD_GRID: t.grid.value if t.grid else "",
                    _FIELD_ATTACHMENTS: list(t.attachment_ids),
                    _FIELD_PLANNING: "0",
                }
            )
        return True

    def _write_task_turn(
        self, task_id: str, session_id: str, run_id: str, task: Task, state: State
    ) -> None:
        """ADR-009：迭代修改写一条 task_turn（uuid5 幂等，PG 失败由 store fail-closed）。"""
        import uuid
        from uuid import uuid5

        store = self._task_store
        turn_store = self._task_turn_store
        if store is None or turn_store is None:
            return
        # S6（review）：advisory 锁原子分配，避免并发同任务迭代撞 turn_index
        turn_index = store.allocate_turn(task_id)
        result_ref = (
            state.terminal.bundle_ref
            if state.terminal and state.terminal.kind is TerminalKind.DONE
            else None
        )
        from services.orchestrator.tasks.pg_stores import TaskTurnData

        turn = TaskTurnData(
            task_id=task_id,
            turn_index=turn_index,
            parent_run_id=run_id,
            run_id=run_id,
            user_intent=_task_str(task.get(_FIELD_DESC)),
            result_bundle_ref=result_ref or "",
            state=TaskState.DONE.value if result_ref else TaskState.FAILED.value,
            id=str(uuid5(uuid.NAMESPACE_URL, f"wp:turn:{task_id}:{turn_index}")),
        )
        turn_store.append(turn)
        store.update_state(
            task_id,
            TaskState.DONE.value if result_ref else TaskState.FAILED.value,
            current_bundle_ref=result_ref,
            last_turn_index=turn_index,
        )

    def _fan_in_if_complete(
        self, session_id: str, user_id: str, parent_run_id: str
    ) -> bool:
        """ADR-006/007 Coordinator fan-in：全部子任务终态时幂等裁决会话终态。

        子任务不各自写会话终态（否则第 2 个 complete 撞 only-RUNNING 冲突）。
        这里查 generation_task 全量，未全终态返回 False；全终态调用确定性
        fan_in，单条会话级 done/error + 部分失败 notice，并回写一次会话状态。
        """
        store = self._task_store
        if store is None:
            return False
        rows = store.list_by_session(session_id)
        children = [r for r in rows if getattr(r, "parent_run_id", "") == parent_run_id]
        if not children:
            return False
        from services.orchestrator.tasks.coordinator import (
            TaskResult,
            emit_fan_in,
            fan_in,
        )
        from services.orchestrator.tasks.events import TypedEventEmitter

        results = tuple(
            TaskResult(
                task_id=str(getattr(r, "id", "")),
                state=TaskState(str(getattr(r, "state", "PENDING"))),
                result_ref=str(r.result_ref or ""),
                reason_code=str(r.reason_code or ""),
                task_index=int(getattr(r, "task_index", 0) or 0),
            )
            for r in children
        )
        verdict = fan_in(results, expected_total=len(children))
        if not verdict.terminal:
            return False
        # 幂等：只有 RUNNING 会话可写终态；重复 fan-in（重投）时 complete 抛
        # ConflictError，这里先读状态跳过，保证"唯一会话级 done"。
        row = self._sessions.get(session_id)
        if row.state not in (SessionState.RUNNING, SessionState.AWAITING_INPUT):
            return True
        if self._seq_allocator is None:
            raise RuntimeError(
                "Coordinator fan-in requires Redis seq allocator "
                "(queue must expose redis URL; no in-memory fallback)"
            )
        emitter = TypedEventEmitter(
            self._deps.event_bus,
            self._seq_allocator,
            session_id,
            parent_run_id,
        )
        emit_fan_in(emitter, verdict, session_id)
        if verdict.state == "COMPLETED":
            bundles = [r.result_ref for r in results if r.state is TaskState.DONE]
            self._sessions.complete(session_id, bundles[0] if bundles else "")
        else:
            self._sessions.fail(
                session_id,
                verdict.reason_code or ReasonCode.RETRY_EXHAUSTED.value,
                f"coordinator/{session_id}/{parent_run_id}",
            )
        return True

    def process_one(self) -> int:
        """消费并处理一条任务；无任务返回 0。

        Returns:
            本次处理的任务数（0 或 1）。
        """
        tasks = self._queue.consume(batch=1)
        if not tasks:
            return 0
        task = tasks[0]
        session_id = _task_str(task.get(_FIELD_SESSION))
        run_id = _task_str(task.get(_FIELD_RUN))
        user_id = _task_str(task.get(_FIELD_USER))
        try:
            # A1：校验链先过（ADR-006 V1 安全门禁：规划任务也先过，命中不进模型）。
            validation = self._check_guardrail(session_id, run_id, user_id, task)
            if validation is not None and not validation.ok:
                return 1  # 硬拒绝/挂起已回写，不进规划也不进模型
            if task.get(_FIELD_PLANNING) == "1" and self._plan_and_fanout(
                session_id, run_id, user_id, task
            ):
                return 1  # N>1 已扇出 / 歧义已挂起（父任务 ACK）

            if validation is None or validation.ok:
                # B2（v1.6）：把 M06 放行结论全字段传进 run()，不重复校验、不丢档位建议。
                # B3：从 SessionRow 读用户澄清回答，回注 State.clarification_answer
                # （M04 answer_clarification 已写 SessionRow，但 dispatcher 恢复时未传给 run）。
                clarification_answer: str | None = None
                if task.get(_FIELD_RESUME) == "1":
                    session_row = self._sessions.get(session_id)
                    clarification_answer = session_row.clarification_answer
                run_input = Input(
                    text=_task_str(task.get(_FIELD_DESC)),
                    grid=_task_str(task.get(_FIELD_GRID)) or None,
                    capability_catalog=_l0_catalog(),  # ADR-003 / B1：L0 目录进上下文
                    model_tier=validation.model_tier if validation else None,
                    evidence_ref=validation.evidence_ref if validation else None,
                    widget_type=(
                        validation.widget_type.value if validation and validation.widget_type else None
                    ),
                    intent_normalized=(
                        validation.intent_normalized.value
                        if validation and validation.intent_normalized else None
                    ),
                    attachments=list(
                        validation.attachment_refs
                        if validation and validation.attachment_refs
                        else _task_attachments(task)
                    ),
                    clarification_answer=clarification_answer,
                    context_user_id=user_id,  # v1.6：记忆端口读取的租户键
                    fact_summary=_read_fact_summary(
                        self._fact_summary_store, session_id
                    ),  # ADR-009：读失败容错为空串（省略该段，不阻断任务）
                )
                # v1.6：生产由 worker 注入真实记忆检索端口（B1/B6/B8），测试不注入则省略数据槽。
                if self._input_enricher is not None:
                    run_input = self._input_enricher(run_input)
                # ADR-006：子任务带 task_id → 更新 generation_task 为 RUNNING。
                task_id = _task_str(task.get(_FIELD_TASK_ID))
                if task_id and self._task_store is not None:
                    self._task_store.update_state(
                        task_id, TaskState.RUNNING.value, run_id=run_id
                    )
                # ADR-011：子任务直接走 run() 单 agent 内核（角色层已移除）。
                state = run(
                    session_id, run_id, run_input, self._deps,
                    cost_sink=self._cost_sink,
                )
                # ADR-009：迭代任务写 task_turn（每轮独立 run_id，旧版本不删）。
                target_task_id = _task_str(task.get(_FIELD_TARGET_TASK))
                if target_task_id and self._task_turn_store is not None:
                    self._write_task_turn(target_task_id, session_id, run_id, task, state)
                if task_id and self._task_store is not None:
                    terminal = state.terminal
                    done = terminal is not None and terminal.kind is TerminalKind.DONE
                    final = TaskState.DONE.value if done else TaskState.FAILED.value
                    self._task_store.update_state(
                        task_id,
                        final,
                        result_ref=terminal.bundle_ref if terminal is not None and done else None,
                        reason_code=terminal.reason_code
                        if terminal is not None and not done
                        else None,
                    )
                parent = _task_str(task.get(_FIELD_PARENT_RUN))
                if task_id and parent:
                    # B3（review）：子任务不各自写会话终态，交 Coordinator fan-in
                    # 在所有子任务终态后幂等裁决（唯一 done + 部分失败 notice）。
                    self._fan_in_if_complete(session_id, user_id, parent)
                else:
                    self._write_back(
                        session_id, user_id, state,
                        last_intent=_task_str(task.get(_FIELD_DESC)),
                        widget_type=(
                            validation.widget_type.value
                            if validation and validation.widget_type else "custom"
                        ),
                    )
        except Exception:
            # review B2：失败不 ACK，留 PEL 由 xautoclaim 重投；超限写死信
            retries = int(_task_str(task.get(_FIELD_RETRIES, "0")) or "0")
            if retries >= _MAX_RETRY:
                self._queue.dead_letter(task)
            raise
        else:
            self._queue.ack(task)  # 仅成功后 ACK
        return 1

    def run_forever(self, poll_s: float = 1.0) -> None:
        """worker 主循环：持续消费任务（阻塞读 + 短暂 sleep 让出）。

        Args:
            poll_s: 空轮询间隔秒（consume 自带 block，此处仅兜底）。
        """
        import time

        while True:
            try:
                processed = self.process_one()
            except Exception as exc:  # noqa: BLE001 - 单任务容错，主循环存活（复审 Major）
                print(f"[dispatcher] task failed (left in PEL): {exc}", flush=True)
                processed = 0
            if processed == 0:
                time.sleep(poll_s)
