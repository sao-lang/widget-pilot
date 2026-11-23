"""多任务/多轮的 ORM 映射（ADR-006/009/010，对齐迁移 0015/0016）。

独立文件，避免与并行改造的 models.py 冲突：只复用其 Base 声明基类与 _TzDateTime
时区时间戳类型，不改动 models.py 既有类。字段逐列对齐迁移，显式 server_default
使未赋值列走 DB 默认值而非被 session.add() 写成 NULL。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from services.control_plane.models import Base, _TzDateTime


class GenerationTask(Base):
    """generation_task：一次父运行规划出的子任务（迁移 0015）。"""

    __tablename__ = "generation_task"
    __table_args__ = (
        Index("ix_generation_task_session_id", "session_id"),
        Index("ix_generation_task_session_state", "session_id", "state"),
        UniqueConstraint(
            "parent_run_id", "task_index", name="uq_generation_task_parent_index"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    """任务 id = uuid5(parent_run_id, task_index) 稳定派生。"""
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    """所属会话（归属/隔离键）。"""
    parent_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    """父运行 id（同批任务共用，规划幂等键的一部分）。"""
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    """子任务实际运行 id；首次消费时原子抢占，一任务一 run。"""
    original_description: Mapped[str] = mapped_column(Text, nullable=False)
    """父级原文（规划输入快照）。"""
    description: Mapped[str] = mapped_column(Text, nullable=False)
    """规划器派生的子任务描述。"""
    task_index: Mapped[int] = mapped_column(Integer, nullable=False)
    """批内序号（从 0 起）。"""
    task_total: Mapped[int] = mapped_column(Integer, nullable=False)
    """批内总数。"""
    grid: Mapped[str | None] = mapped_column(Text, nullable=True)
    """该子任务确认的规格。"""
    attachment_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    """该子任务绑定的附件 id 列表。"""
    state: Mapped[str] = mapped_column(Text, nullable=False)
    """任务状态机值（见 tasks/states.py TaskState）。"""
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """成功产物 bundle 引用。"""
    plan_trace_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """规划复现留证引用。"""
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    """失败原因码。"""
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """失败证据引用。"""
    turn: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    """轮次（ADR-009 任务层语义）。"""
    current_bundle_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """当前轮产物 bundle 引用（ADR-009 完整版）。"""
    last_turn_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    """已完成的最新轮序号。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""
    updated_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """更新时间（UTC）。"""


class TaskTurn(Base):
    """task_turn：子任务的单轮记录（迁移 0016，ADR-009 多轮）。"""

    __tablename__ = "task_turn"
    __table_args__ = (
        Index("ix_task_turn_parent_run_id", "parent_run_id"),
        UniqueConstraint("task_id", "turn_index", name="uq_task_turn_task_turn"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    """轮次 id = uuid5(task_id, turn_index)。"""
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    """所属子任务。"""
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    """轮序号（从 0 起）。"""
    parent_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    """父运行 id（跨任务归属）。"""
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    """该轮实际运行 id。"""
    user_intent: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    """该轮注入的用户意图（澄清/修订）。"""
    fact_summary_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """该轮事实摘要引用。"""
    result_bundle_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """该轮产物 bundle 引用。"""
    cost_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    """该轮成本账引用。"""
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="PENDING"
    )
    """该轮状态（PENDING 起）。"""
    created_at: Mapped[datetime] = mapped_column(
        _TzDateTime, nullable=False, server_default=func.now()
    )
    """创建时间（UTC）。"""
