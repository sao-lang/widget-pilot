"""M04 PG 版会话存储（SessionStore Protocol 的生产实现，§6.1 sessions 表）。

v1.5 ORM 化：手写 SQL → SQLAlchemy ORM（models.Session + db.session_scope）。
乐观锁（S2）用 Core update 的 rowcount 判定，原子语义不变。
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import CursorResult, select, update

from packages.contracts.enums import SessionState
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import Session
from services.control_plane.sessions.stores import SessionRow


class PgSessionStore:
    """PG 会话表：实现 SessionStore Protocol（§6.1 sessions 表，ORM 实现）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN，构造该实例绑定的 sessionmaker。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._factory = make_session_factory(dsn)

    def insert(self, row: SessionRow) -> None:
        """插入会话行（15 字段由 ORM 模型映射，无需列清单）。

        Args:
            row: 完整会话行；主键冲突由上层处理（IntegrityError 透传）。
        """
        obj = Session(
            id=row.id,
            user_id=row.user_id,
            state=row.state.value
            if isinstance(row.state, SessionState)
            else str(row.state),
            title=row.title,
            grid=row.grid,
            grid_source=row.grid_source,
            clarify_count=row.clarify_count,
            clarification_answer=row.clarification_answer,
            original_description=row.original_description,
            last_seq=row.last_seq,
            checkpoint_rev=row.checkpoint_rev,
            run_id=row.run_id,
            reason_code=row.reason_code,
            evidence_ref=row.evidence_ref,
            bundle_ref=row.bundle_ref,  # ADR-009 完整版 / B4：产物引用落库
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        with session_scope(self._factory) as session:
            session.add(obj)

    def get(self, session_id: str) -> SessionRow | None:
        """按 id 查会话，无则 None。

        Args:
            session_id: 会话标识。

        Returns:
            从 ORM 实例收窄的 SessionRow（新对象，改后须显式 update 才落库）。
        """
        with session_scope(self._factory) as session:
            obj = session.scalar(select(Session).where(Session.id == session_id))
        return obj.to_row() if obj is not None else None

    def update(
        self, row: SessionRow, expected_state: SessionState | None = None
    ) -> None:
        """写回全字段（S2：expected_state 非空时加乐观锁 WHERE，0 行抛冲突）。

        Args:
            row: 目标会话行（含新状态与全部字段）。
            expected_state: 前态；非空时 SQL 加 AND state = expected_state，
                并发下仅一个请求命中（0 行命中抛 ValueError → service 折叠 409）。

        Raises:
            ValueError: expected_state 非空且前态不匹配（并发冲突）。
        """
        state_value = (
            row.state.value if isinstance(row.state, SessionState) else str(row.state)
        )
        stmt = (
            update(Session)
            .where(Session.id == row.id)
            .values(
                state=state_value,
                title=row.title,
                grid=row.grid,
                grid_source=row.grid_source,
                clarify_count=row.clarify_count,
                clarification_answer=row.clarification_answer,
                original_description=row.original_description,
                last_seq=row.last_seq,
                checkpoint_rev=row.checkpoint_rev,
                run_id=row.run_id,
                reason_code=row.reason_code,
                evidence_ref=row.evidence_ref,
                bundle_ref=row.bundle_ref,  # ADR-009 完整版 / B4：产物引用落库
                updated_at=row.updated_at,
            )
        )
        if expected_state is not None:
            stmt = stmt.where(Session.state == expected_state.value)
        with session_scope(self._factory) as session:
            # Core UPDATE 的 rowcount 是数据库原始命中数（乐观锁判定依据）；
            # Session.execute 的静态返回类型是 Result，此处收窄为 CursorResult
            result = cast(CursorResult[object], session.execute(stmt))
            if expected_state is not None and result.rowcount == 0:
                raise ValueError("CONFLICT: state changed by another request")

    def list_by_user(self, user_id: str) -> list[SessionRow]:
        """按 user_id 过滤列出会话（租户隔离，§7）。

        Args:
            user_id: 归属用户（服务端 JWT claims）。

        Returns:
            该用户的会话行列表，按 created_at 倒序。
        """
        stmt = (
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.created_at.desc())
        )
        with session_scope(self._factory) as session:
            objs = session.scalars(stmt).all()
        return [obj.to_row() for obj in objs]

    def find_by_run_id(self, run_id: str) -> SessionRow | None:
        """按 run_id 反查会话（review B3：恢复扫描重建完整任务）。

        Args:
            run_id: 执行记录标识（checkpoint 租约里的 run）。

        Returns:
            该 run 归属的会话行；无匹配返回 None。
        """
        with session_scope(self._factory) as session:
            obj = session.scalar(
                select(Session).where(Session.run_id == run_id).limit(1)
            )
        return obj.to_row() if obj is not None else None
