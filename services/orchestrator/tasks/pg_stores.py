"""任务层两张表的 PostgreSQL 真实存储（ADR-006/009/010，fail-closed）。

generation_task / task_turn 的唯一生产实现：SQL 全部由 SQLAlchemy ORM/方言表达
（DSN 经 db.sync_dsn 归一化，不绑定具体 DBAPI）。PG 失败一律抛异常，绝不降级为
内存替身或静默吞错。角色层的 agent_message/agent_workspace 已于 ADR-011 移除。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models_tasks import (
    GenerationTask as GenerationTaskRow,
)
from services.control_plane.models_tasks import (
    TaskTurn as TaskTurnRow,
)

# ---- 存储层数据类（主 agent 从本模块 import） --------------------------------


@dataclass(frozen=True)
class GenerationTaskData:
    """generation_task 一行（迁移 0015 字段的不可变快照）。"""

    id: str
    session_id: str
    parent_run_id: str
    original_description: str
    description: str
    task_index: int
    task_total: int
    state: str
    run_id: str | None = None
    grid: str | None = None
    attachment_ids: tuple[str, ...] = ()
    result_ref: str | None = None
    plan_trace_ref: str | None = None
    reason_code: str | None = None
    evidence_ref: str | None = None
    turn: int = 0
    current_bundle_ref: str | None = None
    last_turn_index: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def task_id(self) -> str:
        """任务 id 别名（= id，便于按 task_id 接线）。"""
        return self.id


@dataclass(frozen=True)
class TaskTurnData:
    """task_turn 一行（迁移 0016；id 缺省由 task_id+turn_index 派生）。"""

    task_id: str
    turn_index: int
    parent_run_id: str
    run_id: str | None = None
    user_intent: str = ""
    fact_summary_ref: str | None = None
    result_bundle_ref: str | None = None
    cost_ref: str | None = None
    state: str = "PENDING"
    id: str = ""
    created_at: datetime | None = None


# ---- 纯函数（派生 / 收窄） -------------------------------------------------


def _text(value: object) -> str:
    """枚举/字符串统一收窄为文本（StrEnum 取 value，不取类名）。"""
    return str(getattr(value, "value", value))


def _turn_id(task_id: str, turn_index: int) -> str:
    """轮次 id = uuid5(task_id, turn_index) 稳定派生（冲突重入不重复落行）。"""
    return str(uuid5(NAMESPACE_URL, f"wp:turn:{task_id}:{turn_index}"))


# ---- ORM 行 → 存储数据类 ---------------------------------------------------


def _task_data(row: GenerationTaskRow) -> GenerationTaskData:
    """ORM 行 → GenerationTaskData（JSONB 列表收窄为 tuple）。"""
    return GenerationTaskData(
        id=str(row.id),
        session_id=str(row.session_id),
        parent_run_id=str(row.parent_run_id),
        original_description=str(row.original_description),
        description=str(row.description),
        task_index=int(row.task_index),
        task_total=int(row.task_total),
        state=str(row.state),
        run_id=str(row.run_id) if isinstance(row.run_id, str) else None,
        grid=str(row.grid) if isinstance(row.grid, str) else None,
        attachment_ids=tuple(str(item) for item in (row.attachment_ids or [])),
        result_ref=str(row.result_ref) if isinstance(row.result_ref, str) else None,
        plan_trace_ref=str(row.plan_trace_ref)
        if isinstance(row.plan_trace_ref, str)
        else None,
        reason_code=str(row.reason_code) if isinstance(row.reason_code, str) else None,
        evidence_ref=str(row.evidence_ref) if isinstance(row.evidence_ref, str) else None,
        turn=int(row.turn),
        current_bundle_ref=str(row.current_bundle_ref)
        if isinstance(row.current_bundle_ref, str)
        else None,
        last_turn_index=int(row.last_turn_index),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _turn_data(row: TaskTurnRow) -> TaskTurnData:
    """ORM 行 → TaskTurnData。"""
    return TaskTurnData(
        id=str(row.id),
        task_id=str(row.task_id),
        turn_index=int(row.turn_index),
        parent_run_id=str(row.parent_run_id),
        run_id=str(row.run_id) if isinstance(row.run_id, str) else None,
        user_intent=str(row.user_intent),
        fact_summary_ref=str(row.fact_summary_ref)
        if isinstance(row.fact_summary_ref, str)
        else None,
        result_bundle_ref=str(row.result_bundle_ref)
        if isinstance(row.result_bundle_ref, str)
        else None,
        cost_ref=str(row.cost_ref) if isinstance(row.cost_ref, str) else None,
        state=str(row.state),
        created_at=row.created_at,
    )


# ---- PG stores -------------------------------------------------------------


class PgGenerationTaskStore:
    """generation_task 的真实 PG 存储（幂等建行 + 原子抢占 run）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（任意驱动前缀由 db.sync_dsn 归一化）。"""
        self._factory = make_session_factory(dsn)

    def create_many(self, tasks: Sequence[GenerationTaskData]) -> None:
        """批量落子任务（ON CONFLICT DO NOTHING，重规划重复调用幂等）。"""
        if not tasks:
            return
        values: list[dict[str, object]] = []
        for task in tasks:
            row: dict[str, object] = {
                "id": task.id,
                "session_id": task.session_id,
                "parent_run_id": task.parent_run_id,
                "run_id": task.run_id,
                "original_description": task.original_description,
                "description": task.description,
                "task_index": task.task_index,
                "task_total": task.task_total,
                "grid": task.grid,
                "attachment_ids": list(task.attachment_ids),
                "state": task.state,
                "result_ref": task.result_ref,
                "plan_trace_ref": task.plan_trace_ref,
                "reason_code": task.reason_code,
                "evidence_ref": task.evidence_ref,
                "turn": task.turn,
                "current_bundle_ref": task.current_bundle_ref,
                "last_turn_index": task.last_turn_index,
            }
            if task.created_at is not None:
                row["created_at"] = task.created_at
            if task.updated_at is not None:
                row["updated_at"] = task.updated_at
            values.append(row)
        with session_scope(self._factory) as session:
            session.execute(
                pg_insert(GenerationTaskRow).values(values).on_conflict_do_nothing()
            )

    def get(self, task_id: str) -> GenerationTaskData | None:
        """按 task id 读一行；不存在返回 None。"""
        with session_scope(self._factory) as session:
            row = session.scalar(
                select(GenerationTaskRow).where(GenerationTaskRow.id == task_id)
            )
        return _task_data(row) if row is not None else None

    def list_by_session(self, session_id: str) -> list[GenerationTaskData]:
        """按会话列出全部子任务（task_index 升序）。"""
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(GenerationTaskRow)
                .where(GenerationTaskRow.session_id == session_id)
                .order_by(GenerationTaskRow.task_index)
            ).all()
        return [_task_data(row) for row in rows]

    def update_state(
        self,
        task_id: str,
        state: str,
        *,
        run_id: str | None = None,
        result_ref: str | None = None,
        reason_code: str | None = None,
        evidence_ref: str | None = None,
        current_bundle_ref: str | None = None,
        last_turn_index: int | None = None,
        turn: int | None = None,
    ) -> None:
        """更新状态 + 非空附带字段 + updated_at（None 字段保持不变）。"""
        values: dict[str, object] = {"state": state, "updated_at": func.now()}
        if run_id is not None:
            values["run_id"] = run_id
        if result_ref is not None:
            values["result_ref"] = result_ref
        if reason_code is not None:
            values["reason_code"] = reason_code
        if evidence_ref is not None:
            values["evidence_ref"] = evidence_ref
        if current_bundle_ref is not None:
            values["current_bundle_ref"] = current_bundle_ref
        if last_turn_index is not None:
            values["last_turn_index"] = last_turn_index
        if turn is not None:
            values["turn"] = turn
        with session_scope(self._factory) as session:
            session.execute(
                update(GenerationTaskRow)
                .where(GenerationTaskRow.id == task_id)
                .values(**values)
            )

    def allocate_turn(self, task_id: str) -> int:
        """S6（review）：按 task_id advisory 锁原子分配下一个 turn_index。

        读改写（get.last_turn_index + 1 → append）在并发同任务迭代下会撞同一
        turn_index 并被 ON CONFLICT DO NOTHING 静默吞掉一轮。这里用 PG 事务内
        advisory lock 串行化 + 原子 UPDATE ... RETURNING，保证轮次不丢。
        """
        with session_scope(self._factory) as session:
            session.execute(
                select(func.pg_advisory_xact_lock(func.hashtext(task_id)))
            )
            row = session.scalar(
                update(GenerationTaskRow)
                .where(GenerationTaskRow.id == task_id)
                .values(
                    last_turn_index=GenerationTaskRow.last_turn_index + 1,
                    updated_at=func.now(),
                )
                .returning(GenerationTaskRow.last_turn_index)
            )
        if row is None:
            raise RuntimeError(f"generation task not found for turn allocation: {task_id}")
        return int(row)

    def claim_run(self, task_id: str, run_id: str) -> bool:
        """原子抢占执行权：仅 run_id IS NULL 时写入，返回是否抢到。"""
        stmt = (
            update(GenerationTaskRow)
            .where(
                GenerationTaskRow.id == task_id,
                GenerationTaskRow.run_id.is_(None),
            )
            .values(run_id=run_id, updated_at=func.now())
        )
        with session_scope(self._factory) as session:
            result = cast(CursorResult[object], session.execute(stmt))
            return result.rowcount == 1

    def summary(self, session_id: str) -> list[dict[str, object]]:
        """会话级子任务摘要（Coordinator fan-in 用精简投影）。"""
        with session_scope(self._factory) as session:
            rows = session.execute(
                select(
                    GenerationTaskRow.id,
                    GenerationTaskRow.task_index,
                    GenerationTaskRow.task_total,
                    GenerationTaskRow.state,
                    GenerationTaskRow.description,
                    GenerationTaskRow.turn,
                    GenerationTaskRow.result_ref,
                    GenerationTaskRow.reason_code,
                    GenerationTaskRow.current_bundle_ref,
                    GenerationTaskRow.last_turn_index,
                )
                .where(GenerationTaskRow.session_id == session_id)
                .order_by(GenerationTaskRow.task_index)
            ).all()
        return [
            {
                "task_id": str(row.id),
                "task_index": int(row.task_index),
                "task_total": int(row.task_total),
                "state": str(row.state),
                "description": str(row.description),
                "turn": int(row.turn),
                "result_ref": str(row.result_ref)
                if isinstance(row.result_ref, str)
                else None,
                "reason_code": str(row.reason_code)
                if isinstance(row.reason_code, str)
                else None,
                "current_bundle_ref": str(row.current_bundle_ref)
                if isinstance(row.current_bundle_ref, str)
                else None,
                "last_turn_index": int(row.last_turn_index),
            }
            for row in rows
        ]


class PgTaskTurnStore:
    """task_turn 的真实 PG 存储（只追加 + 幂等 + 按轮次读）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（任意驱动前缀由 db.sync_dsn 归一化）。"""
        self._factory = make_session_factory(dsn)

    def append(self, turn: TaskTurnData) -> None:
        """追加一轮（同 (task_id, turn_index) 重复写幂等跳过）。"""
        values: dict[str, object] = {
            "id": turn.id or _turn_id(turn.task_id, turn.turn_index),
            "task_id": turn.task_id,
            "turn_index": turn.turn_index,
            "parent_run_id": turn.parent_run_id,
            "run_id": turn.run_id,
            "user_intent": turn.user_intent,
            "fact_summary_ref": turn.fact_summary_ref,
            "result_bundle_ref": turn.result_bundle_ref,
            "cost_ref": turn.cost_ref,
            "state": turn.state,
        }
        if turn.created_at is not None:
            values["created_at"] = turn.created_at
        with session_scope(self._factory) as session:
            session.execute(
                pg_insert(TaskTurnRow).values(values).on_conflict_do_nothing()
            )

    def latest(self, task_id: str) -> TaskTurnData | None:
        """读最新一轮；该任务尚无轮次返回 None。"""
        with session_scope(self._factory) as session:
            row = session.scalar(
                select(TaskTurnRow)
                .where(TaskTurnRow.task_id == task_id)
                .order_by(TaskTurnRow.turn_index.desc())
                .limit(1)
            )
        return _turn_data(row) if row is not None else None

    def list_by_task(self, task_id: str) -> list[TaskTurnData]:
        """按轮次升序列出该任务全部轮次。"""
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(TaskTurnRow)
                .where(TaskTurnRow.task_id == task_id)
                .order_by(TaskTurnRow.turn_index)
            ).all()
        return [_turn_data(row) for row in rows]
