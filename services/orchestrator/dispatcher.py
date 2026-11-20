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
from collections.abc import Callable
from typing import Final, cast

import redis

from packages.agent_core.injection import Deps
from packages.agent_core.loop.run import run
from packages.agent_core.types import Input, State, TerminalKind
from packages.contracts.enums import ReasonCode, SessionState
from packages.contracts.types import TaskFields
from services.control_plane.sessions.service import SessionService
from services.guardrails.types import ValidationResult

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
    ) -> None:
        """注入任务队列、执行依赖、会话服务与校验链（状态回写入口）。

        Args:
            queue: 任务队列（消费 + ACK + 死信）。
            deps: M03 run() 的全部依赖（生产真实 / 测试 fake）。
            sessions: 会话状态机服务（终态回写）。
            validator: M06 校验链入口（A1）；生产注入真实装配，测试注入放行桩。
        """
        self._queue = queue
        self._deps = deps
        self._sessions = sessions
        self._validator = validator

    def _write_back(self, session_id: str, user_id: str, state: State) -> None:
        """把 run() 终态映射回 SessionService（§5.2 状态迁移集中一处）。

        Args:
            session_id: 会话标识。
            user_id: 会话 owner（租户校验用，不再传空——review B1）。
            state: run() 返回的最终 State。
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
            if not bundle_ref:
                # review B6：M08 未接入前模型自报产物不可信，转失败不假绿
                self._sessions.fail(
                    session_id,
                    _NO_BUNDLE_REASON,
                    f"{_EVIDENCE_PREFIX}/{session_id}/{state.run_id}/no-bundle",
                )
            else:
                self._sessions.complete(session_id, bundle_ref)
        else:
            # FAILED / HANDOFF：原因码 + 证据引用缺一不可（A14）
            reason = state.terminal.reason_code or "HUMAN_REQUIRED"
            evidence = state.terminal.evidence_ref or (
                f"{_EVIDENCE_PREFIX}/{session_id}/{state.run_id}"
            )
            self._sessions.fail(session_id, reason, evidence)

    def _check_guardrail(
        self, session_id: str, run_id: str, user_id: str, task: Task
    ) -> bool:
        """A1：run() 前过 M06 校验链；返回是否已确定性处理（无需进模型）。

        把任务字段收敛成一个 dict 传给装配层绑定的 validator 闭包——dispatcher
        不感知 embedder/store 等重依赖（由 worker 装配时绑定）。命中/挂起在此
        直接回写会话状态，返回 True；放行或未注入返回 False。

        Args:
            session_id: 会话标识。
            run_id: 执行标识。
            user_id: 会话 owner（回写租户校验用）。
            task: 原始任务字段（校验入参来源）。

        Returns:
            True 表示校验已回写终态/挂起态，调用方须跳过 run()；
            False 表示放行或未注入 validator（走旧行为）。

        Raises:
            SessionError / ConflictError: 状态回写冲突（与 _write_back 同口径）。
        """
        # M5（v1.8）：恢复任务（worker 从过期租约扫描重建，description 为空）跳过
        # 校验链——首次提交已过护栏，恢复是 checkpoint 续跑而非新请求，
        # 否则空描述会被判 INPUT_INVALID 误挂起，跨实例恢复能力失效。
        if task.get(_FIELD_RESUME) == "1":
            return False
        # 任务字段来自 JSON 边界，逐项归一到 TaskFields 的确定形状；
        # attachment_ids 兼容畸形标量输入（旧装配层也做 list 包裹），保持行为等价。
        raw_att_ids = task.get(_FIELD_ATTACHMENTS, [])
        attachment_ids = (
            raw_att_ids
            if isinstance(raw_att_ids, list)
            else ([raw_att_ids] if raw_att_ids else [])
        )
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
        }
        result = self._validator(str(task.get(_FIELD_DESC, "")), fields)
        if result.ok:
            return False
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
            # A1：校验链先过；命中/挂起时已由 _check_guardrail 回写状态，跳过模型。
            handled = self._check_guardrail(session_id, run_id, user_id, task)
            if not handled:
                state = run(
                    session_id,
                    run_id,
                    Input(
                        text=_task_str(task.get(_FIELD_DESC)),
                        grid=_task_str(task.get(_FIELD_GRID)) or None,
                    ),
                    self._deps,
                )
                self._write_back(session_id, user_id, state)
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
