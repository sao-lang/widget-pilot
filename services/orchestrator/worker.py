"""orchestrator worker 主进程（B1b：真实编排回路 + 进程健康探针）。

用生产 Deps（真实 PG checkpoint/lease、Redis 幂等/事件/任务队列、真实模型网关）
构造 Dispatcher，持续消费任务驱动 agent loop；同时每 10s 刷新心跳文件供
Docker HEALTHCHECK 探测（M01 §3.3 无端口 worker 探针）。

模型网关从 env 读取，缺失时 fail-fast 退出——禁止静默降级 fake。
恢复扫描：周期把过期租约的 run 重新入队（worker 被杀后另一实例接管）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from packages.agent_core.types import Input, State
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import Checkpoint
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.orchestrator.deps import build_deps
from services.orchestrator.dispatcher import (
    TASK_QUEUE_KEY,
    Dispatcher,
    RedisTaskQueue,
    Task,
)

HEARTBEAT_INTERVAL_S = 10
SCAN_INTERVAL_S = 30
_ENV_REDIS_URL = "REDIS_URL"
_ENV_DATABASE_URL = "DATABASE_URL"
_ENV_OWNER = "WORKER_OWNER"
_DEFAULT_OWNER = "orchestrator-1"


def scan_expired_tasks(dsn: str) -> list[Task]:
    """扫过期租约的 run，反查会话构造完整恢复任务（review B3）。

    只带回真实 session_id/user_id，避免恢复任务因空 session_id 回写失败。

    Args:
        dsn: PG 连接串。

    Returns:
        恢复任务列表（含 session_id/user_id/run_id），无匹配会话的 run 跳过。
    """
    with session_scope(make_session_factory(dsn)) as session:
        run_ids = session.scalars(
            select(Checkpoint.run_id)
            .where(Checkpoint.lease_until < datetime.now(UTC))
            .where(Checkpoint.lease_owner.is_not(None))
            .distinct()
        ).all()
    sessions = PgSessionStore(dsn)
    tasks: list[Task] = []
    for raw_run_id in run_ids:
        run_id = str(raw_run_id)
        row = sessions.find_by_run_id(run_id)
        if row is None:
            continue  # 会话已删/无匹配：跳过，避免构造空 session 任务
        tasks.append(
            {
                "session_id": row.id,
                "user_id": row.user_id,
                "run_id": run_id,
                "description": "",
                # M5（v1.8）：恢复任务标记 resume=1——首次提交已过校验链，
                # 恢复是续跑而非新请求；dispatcher 据此跳过校验避免空描述被误挂起。
                "resume": "1",
            }
        )
    return tasks


async def main() -> None:
    """启动编排主循环 + 心跳 + 恢复扫描。

    Raises:
        RuntimeError: 模型网关 env 未配齐（build_deps fail-fast，不降级 fake）。
    """
    hb_path = Path(os.getenv("HEARTBEAT_FILE", "/tmp/orchestrator.heartbeat"))
    try:
        redis_url = os.environ[_ENV_REDIS_URL]
        dsn = os.environ[_ENV_DATABASE_URL]
    except KeyError as exc:
        raise RuntimeError(f"worker requires {exc.args[0]} env var") from exc
    owner = os.getenv(_ENV_OWNER, _DEFAULT_OWNER)

    # 延迟导入：避免与 control_plane 健康壳的循环依赖
    from services.control_plane.attachments.pg_stores import PgAttachmentStore
    from services.control_plane.audit.pg_ledger import PgAuditLedger
    from services.control_plane.sessions.pg_stores import PgSessionStore
    from services.control_plane.sessions.service import SessionService
    from services.orchestrator.cost.meter_adapter import accrue
    from services.orchestrator.cost.pg_ledger import PgCostLedger
    from services.orchestrator.cost.ports import CostEntryMeta
    from services.orchestrator.guardrail_adapter import build_validator
    from services.orchestrator.memory.adapters import (
        PreferenceInjectionAdapter,
        ProcedureHintAdapter,
        high_confidence_grids,
    )
    from services.orchestrator.memory.fact_summary_store import PgFactSummaryStore
    from services.orchestrator.memory.pg_stores import (
        PgPreferenceStore,
        PgProcedureStore,
    )
    from services.orchestrator.memory.repro import ReproInputs

    queue = RedisTaskQueue(redis_url, TASK_QUEUE_KEY, consumer=owner)
    sessions = SessionService(PgSessionStore(dsn), audit=PgAuditLedger(dsn))
    # A1（v1.8）：把 M06 校验链接进生产编排入口（run() 前过护栏）。
    # L2/L3/L4 真实模型端口不注入（M20/M07 提供，属排除项），走默认结构化兜底。
    attachment_store = PgAttachmentStore(dsn)
    # B1/B6（v1.6）：注入 M07 偏好/程序记忆 Store（真实 PG），
    # 使 D-12「记忆读了就用」与 D-14 采纳软提示在生产生效。
    preference_store = PgPreferenceStore(dsn)
    procedure_store = PgProcedureStore(dsn)
    preference_port = PreferenceInjectionAdapter(preference_store)
    procedure_port = ProcedureHintAdapter(procedure_store)

    def _preferred_grids_get(user_id: str) -> list[str]:
        """按 user_id 读高置信 preferred_grids（B1，偏好是建议非门禁）。"""
        return high_confidence_grids(preference_store.get(user_id))

    def _enrich_input(run_input: Input) -> Input:
        """把真实记忆端口注入 run 入参（v1.6；检索/附件端口待 M10/M05 落地）。

        Raises:
            RuntimeError: 生产关键端口（偏好/程序记忆）缺失——禁静默降级为空槽。
        """
        if preference_port is None or procedure_port is None:
            raise RuntimeError("M07 生产装配缺少关键记忆端口（Preference/Procedure）")
        return replace(
            run_input,
            preference_port=preference_port,
            procedure_port=procedure_port,
        )

    # B7：真实 PG 成本账本 + 终态落账回调（repro 指纹含真实 preference_version）。
    # 账本只从 deps.cost_ledger 取（单一事实源，不留闭包旁路端口）。
    deps = build_deps(
        dsn, redis_url, owner=owner, cost_ledger=PgCostLedger(dsn)
    )  # 缺模型配置 → fail-fast
    if deps.cost_ledger is None:
        raise RuntimeError("cost_ledger 注入失败：build_deps 未挂载成本账本")
    cost_ledger = deps.cost_ledger  # 闭包内局部收窄（Python 闭包不保留外部 guard 收窄）

    def _cost_sink(state: State) -> None:
        """终态落成本账（B7）：user_id 与 preference_version 从真实会话/偏好读。"""
        row = sessions.get(state.session_id)
        pref_version = preference_store.get(row.user_id).version
        accrue(
            state,
            deps,
            cost_ledger,
            CostEntryMeta(
                user_id=row.user_id,
                repro_inputs=ReproInputs(preference_version=pref_version),
            ),
        )

    # A3（v1.8）：传 dsn 注入 PG 留证（guard_hit + IntentRouteLog；能力审计走 loop 内适配器）；
    # guard_hit PG 不可用时保守拒绝放行。表由迁移 0005/0007 建立（0009 删技能预取表）。
    validator = build_validator(
        attachment_store.get, redis_url, dsn=dsn,
        preferred_grids_get=_preferred_grids_get,
    )
    # ADR-006/007/009：多任务/多 Agent/多轮真实 PG 存储端口（生产必注入，无内存桩）。
    from services.orchestrator.tasks.model_ports import (
        GatewayGenerator,
        GatewayPlanLLM,
        GatewayReviewer,
    )
    from services.orchestrator.tasks.pg_stores import (
        PgAgentMessageStore,
        PgAgentWorkspace,
        PgGenerationTaskStore,
        PgTaskTurnStore,
    )
    from services.orchestrator.tasks.roles import run_role_loop
    from services.orchestrator.tasks.router import Router

    task_store = PgGenerationTaskStore(dsn)
    task_turn_store = PgTaskTurnStore(dsn)
    msg_store = PgAgentMessageStore(dsn)
    workspace = PgAgentWorkspace(dsn)
    router = Router(msg_store, workspace)

    from services.orchestrator.tasks.events import RedisSeqAllocator, TypedEventEmitter

    seq_allocator = RedisSeqAllocator(redis_url)

    def _role_runner(session_id: str, task_id: str, description: str) -> tuple[bool, str, str]:
        """N>1 子任务的 Generator/Reviewer 角色循环（生产真实模型端口）。"""
        emitter = TypedEventEmitter(deps.event_bus, seq_allocator, session_id, f"role-{task_id}", task_id)
        outcome = run_role_loop(
            session_id=session_id, task_id=task_id, description=description,
            trace_id=task_id, generator=GatewayGenerator(deps.model),
            reviewer=GatewayReviewer(deps.model), router=router,
            events=emitter,
        )
        return outcome.passed, outcome.artifact_ref, outcome.reason_code

    dispatcher = Dispatcher(
        queue=queue, deps=deps, sessions=sessions, validator=validator,
        input_enricher=_enrich_input, cost_sink=_cost_sink,
        fact_summary_store=PgFactSummaryStore(dsn),  # ADR-009：多轮事实真实 PG 存储
        task_store=task_store,
        plan_llm=GatewayPlanLLM(deps.model),
        role_runner=_role_runner,
        task_turn_store=task_turn_store,
    )

    print(
        f"[orchestrator] worker started owner={owner} (real orchestration loop)",
        flush=True,
    )
    loop = asyncio.get_running_loop()
    last_hb = time.monotonic()
    last_scan = time.monotonic()
    while True:
        # consume 是同步阻塞（Redis XREADGROUP 短 block）；放线程池避免卡住事件循环
        # 复审 Major：单任务失败（run() 抛错）只记日志继续——任务留 PEL 由重投/死信
        # 处理，不能击穿主循环导致进程崩溃-重启循环。
        try:
            processed = await loop.run_in_executor(None, dispatcher.process_one)
        except Exception as exc:  # noqa: BLE001 - 单任务容错，主循环必须存活
            print(f"[orchestrator] task failed (left in PEL): {exc}", flush=True)
            processed = 0
        now = time.monotonic()
        if now - last_hb >= HEARTBEAT_INTERVAL_S:
            hb_path.parent.mkdir(parents=True, exist_ok=True)
            hb_path.write_text(
                json.dumps(
                    {"ts": time.time(), "service": "orchestrator", "owner": owner}
                )
            )
            last_hb = now
        if now - last_scan >= SCAN_INTERVAL_S:
            # 恢复扫描失败只记日志，不崩主循环（租约最终会过期重试）
            try:
                queue.replay_expired(scan_expired_tasks(dsn))
            except Exception as exc:  # noqa: BLE001 - 后台扫描容错，主循环继续
                print(f"[orchestrator] scan_expired failed: {exc}", flush=True)
            last_scan = now
        if processed == 0:
            await asyncio.sleep(0.2)  # 空转让出 CPU（consume 自带 1s block）


if __name__ == "__main__":
    asyncio.run(main())
