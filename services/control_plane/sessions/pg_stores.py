"""M04 PG 版会话存储（SessionStore Protocol 的生产实现，§6.1 sessions 表）。

与 InMemorySessionStore 同协议，供 SessionService 无差别注入（规则 8/9）。
SessionRow 全字段（含 v1.3/v1.4 的 grid_source/clarify_count/
clarification_answer/reason_code/evidence_ref）逐列映射，状态枚举用值存取。
SQL 用 psycopg.sql.SQL 组合（静态列名常量），既满足类型检查又避免拼接注入。
"""
from __future__ import annotations

from datetime import UTC, datetime

import psycopg
from psycopg import sql

from packages.contracts.enums import SessionState
from services.control_plane.sessions.stores import SessionRow

# SELECT 列清单（集中一处，INSERT/SELECT 复用，避免列漂移）
_COLS: str = (
    "id, user_id, state, title, grid, grid_source, clarify_count, "
    "clarification_answer, last_seq, checkpoint_rev, run_id, "
    "reason_code, evidence_ref, created_at, updated_at"
)
_SELECT_COLS = sql.SQL(_COLS)


class PgSessionStore:
    """PG 会话表：实现 SessionStore Protocol（§6.1 sessions 表）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN；每操作短连接。"""
        self._dsn = dsn

    def insert(self, row: SessionRow) -> None:
        """插入会话行（15 字段逐列映射）。"""
        query = sql.SQL("INSERT INTO sessions ({}) VALUES ").format(_SELECT_COLS) + sql.SQL(
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(query, _row_params(row))

    def get(self, session_id: str) -> SessionRow | None:
        """按 id 查会话，无则 None。"""
        query = sql.SQL("SELECT {} FROM sessions WHERE id = %s").format(_SELECT_COLS)
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(query, (session_id,))
            row = cur.fetchone()
        return _to_session_row(row) if row else None

    def update(self, row: SessionRow) -> None:
        """写回全字段（生产 UPDATE；updated_at 由服务层刷新后带入）。"""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET state = %s, title = %s, grid = %s, "
                "grid_source = %s, clarify_count = %s, clarification_answer = %s, "
                "last_seq = %s, checkpoint_rev = %s, run_id = %s, "
                "reason_code = %s, evidence_ref = %s, updated_at = %s "
                "WHERE id = %s",
                (
                    row.state.value
                    if isinstance(row.state, SessionState)
                    else str(row.state),
                    row.title,
                    row.grid,
                    row.grid_source,
                    row.clarify_count,
                    row.clarification_answer,
                    row.last_seq,
                    row.checkpoint_rev,
                    row.run_id,
                    row.reason_code,
                    row.evidence_ref,
                    row.updated_at,
                    row.id,
                ),
            )

    def list_by_user(self, user_id: str) -> list[SessionRow]:
        """按 user_id 过滤列出会话（租户隔离，§7）。"""
        query = sql.SQL(
            "SELECT {} FROM sessions WHERE user_id = %s ORDER BY created_at DESC"
        ).format(_SELECT_COLS)
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(query, (user_id,))
            rows = cur.fetchall()
        return [_to_session_row(r) for r in rows]


def _row_params(row: SessionRow) -> tuple[object, ...]:
    """SessionRow → INSERT 参数元组（顺序与 _COLS 一致）。"""
    state = row.state.value if isinstance(row.state, SessionState) else str(row.state)
    return (
        row.id,
        row.user_id,
        state,
        row.title,
        row.grid,
        row.grid_source,
        row.clarify_count,
        row.clarification_answer,
        row.last_seq,
        row.checkpoint_rev,
        row.run_id,
        row.reason_code,
        row.evidence_ref,
        row.created_at,
        row.updated_at,
    )


def _as_int(value: object) -> int:
    """把 PG 返回的数值列收窄为 int（异常/None 回退 0，fail-safe）。"""
    return int(value) if isinstance(value, (int, str)) else 0


def _to_session_row(row: tuple[object, ...]) -> SessionRow:
    """PG 行 → SessionRow（动态边界单点收窄，编码规则 6）。

    未知 state 显式回退 DRAFT（规则 5：未知值回退安全成员），不让脏数据
    把会话带入不可控态。
    """
    raw_state = str(row[2])
    try:
        state = SessionState(raw_state)
    except ValueError:
        state = SessionState.DRAFT
    return SessionRow(
        id=str(row[0]),
        user_id=str(row[1]),
        state=state,
        title=str(row[3] or ""),
        grid=row[4] if isinstance(row[4], str) else None,
        grid_source=row[5] if isinstance(row[5], str) else None,
        clarify_count=_as_int(row[6]),
        clarification_answer=row[7] if isinstance(row[7], str) else None,
        last_seq=_as_int(row[8]),
        checkpoint_rev=_as_int(row[9]),
        run_id=row[10] if isinstance(row[10], str) else None,
        reason_code=row[11] if isinstance(row[11], str) else None,
        evidence_ref=row[12] if isinstance(row[12], str) else None,
        created_at=row[13] if isinstance(row[13], datetime) else datetime.now(UTC),
        updated_at=row[14] if isinstance(row[14], datetime) else datetime.now(UTC),
    )
