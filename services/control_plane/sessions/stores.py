"""M04 会话存储（§6.1 Session/Run 实体的持久化抽象）。

生产实现只有 PG（alembic 迁移）；测试内存替身位于 `tests/support/control_plane.py`。
租户隔离键 user_id 贯穿每条记录，与 TenantGuard 配合（§7）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from packages.contracts.enums import SessionState


@dataclass(slots=True)
class SessionRow:
    """Session 表的一行（§6.1 字段，含 v1.3/v1.4 新增字段）。"""

    id: str
    """会话标识（uuid）。"""
    user_id: str
    """租户隔离键。"""
    state: SessionState
    """状态机当前态。"""
    title: str = ""
    """会话标题。"""
    grid: str | None = None
    """可选规格（v1.3 D-6 可空，进入生成回路前必非空）。"""
    grid_source: str | None = None
    """规格来源溯源（selector/explicit_text/clarified）。"""
    clarify_count: int = 0
    """已回答澄清次数（上限 2，D-7）。"""
    clarification_answer: str | None = None
    """当前待回注的澄清答案（P0-2 落点，跨天恢复可还原）。"""
    original_description: str = ""
    """首次提交的用户原始描述（R3-1，v1.8）：M06 护栏前置挂起时不产生 checkpoint，
    澄清重入须用它恢复模型上下文，否则生成只收到规格答案（如"四行四列"）。"""
    last_seq: int = 0
    """SSE 重连锚点（每会话单调）。"""
    checkpoint_rev: int = 0
    """当前 checkpoint 版本（interrupt 幂等断言点）。"""
    run_id: str | None = None
    """当前执行记录标识（恢复锚点）。"""
    reason_code: str | None = None
    """FAILED 的原因码（§4.4：失败必带 code，前端据此展示具体原因）。"""
    evidence_ref: str | None = None
    """FAILED 的证据引用（§4.4：禁止只返回「失败」，必带证据）。"""
    bundle_ref: str | None = None
    """当前轮产物 bundle 引用（ADR-009 完整版，迁移 0017；可选保持兼容）。"""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """创建时间（UTC）。"""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """更新时间（UTC）。"""


@runtime_checkable
class SessionStore(Protocol):
    """会话存储协议（编码规则 8/9：生产 PG 与测试替身可插拔）。"""

    def insert(self, row: SessionRow) -> None:
        """插入会话行。"""
        ...

    def get(self, session_id: str) -> SessionRow | None:
        """按 id 查会话，无则 None。"""
        ...

    def update(
        self, row: SessionRow, expected_state: SessionState | None = None
    ) -> None:
        """写回变更后的会话行；expected_state 非空时为乐观锁（不符抛冲突）。"""
        ...

    def list_by_user(self, user_id: str) -> list[SessionRow]:
        """按 user_id 过滤列出会话（租户隔离）。"""
        ...
