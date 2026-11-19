"""M04 会话存储（§6.1 Session/Run 实体的持久化抽象）。

生产实现为 PG（alembic 迁移）；内存实现供 §8.1 无 Docker 环境跑全用例。
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
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """创建时间（UTC）。"""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """更新时间（UTC）。"""


@runtime_checkable
class SessionStore(Protocol):
    """会话存储协议（编码规则 8/9：PG/内存双实现可插拔）。"""

    def insert(self, row: SessionRow) -> None:
        """插入会话行。"""
        ...

    def get(self, session_id: str) -> SessionRow | None:
        """按 id 查会话，无则 None。"""
        ...

    def update(self, row: SessionRow) -> None:
        """写回变更后的会话行。"""
        ...

    def list_by_user(self, user_id: str) -> list[SessionRow]:
        """按 user_id 过滤列出会话（租户隔离）。"""
        ...


class InMemorySessionStore:
    """内存会话表：按 id 索引，保留插入顺序供列表查询。"""

    def __init__(self) -> None:
        """初始化空表与 id 索引。"""
        self._rows: dict[str, SessionRow] = {}

    def insert(self, row: SessionRow) -> None:
        """插入会话行（id 服务端生成，重复覆盖由上层保证不重复）。"""
        self._rows[row.id] = row

    def get(self, session_id: str) -> SessionRow | None:
        """按 id 查会话，无则返回 None。"""
        return self._rows.get(session_id)

    def update(self, row: SessionRow) -> None:
        """写回变更后的会话行（内存即覆盖；生产为 PG UPDATE）。"""
        row.updated_at = datetime.now(UTC)
        self._rows[row.id] = row

    def list_by_user(self, user_id: str) -> list[SessionRow]:
        """按 user_id 过滤列出会话（租户隔离，§7）。"""
        return [r for r in self._rows.values() if r.user_id == user_id]
