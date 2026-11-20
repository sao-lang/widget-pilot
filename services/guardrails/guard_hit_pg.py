"""guard_hit 的 PostgreSQL 只追加存储（runbook §2 定稿，2026-11-21 补齐）。

与内存版接口一致（log_hit / verify_chain / false_positive_rate / rows），
但落 guard_hit 表 + prev_hash hash 链；写入在单事务内对末行加行锁，防并发断链。

**不可用语义（M06 §2.3）**：PG 连接/写入失败时**抛异常**，由装配层保守拒绝放行——
绝不降级为内存替身（无证据放行）。这与三表日志（append 失败只丢证据）相反，
因为 guard_hit 是「命中即不放行」路径的法定留证。
"""

from __future__ import annotations

import hashlib
from typing import Any, Final, cast

import psycopg
from psycopg.rows import DictRow, dict_row

from services.guardrails.guard_hit import GuardHitRow
from services.guardrails.types import GuardHitType, InputGuardHit

_TABLE: Final[str] = "guard_hit"
_GENESIS: Final[str] = "0" * 64
_ASYNCPG_PREFIX: Final[str] = "postgresql+asyncpg://"
# guard_hit 追加的全局事务锁键（任意固定 bigint；只用于串行化本表 append）。
_ADVISORY_KEY: Final[int] = 2026112101


def _sync_dsn(url: str) -> str:
    """asyncpg DSN（compose 生产格式）→ psycopg 同步 DSN（B1，v1.8）。

    Args:
        url: 原始 DSN（可能是 SQLAlchemy asyncpg 格式）。

    Returns:
        psycopg 可直接连接的同步 DSN。
    """
    return (
        url.replace(_ASYNCPG_PREFIX, "postgresql://", 1)
        if url.startswith(_ASYNCPG_PREFIX)
        else url
    )


def _connect_dict(dsn: str) -> psycopg.Connection[DictRow]:
    """开返回 dict 行的同步连接（psycopg 类型桩未把 row_factory 纳入泛型推断）。"""
    # 反射边界豁免：dict_row 由 psycopg 运行时注入行工厂，类型桩无法表达其行类型，
    # 故在此一处 cast(Any, dict_row) 收口，下游只用 DictRow 具名视图。
    return cast(
        psycopg.Connection[DictRow],
        psycopg.connect(dsn, row_factory=cast(Any, dict_row)),
    )


def _chain_hash(row: GuardHitRow) -> str:
    """复算行 hash（与内存版 _chain_hash 逐字段一致，防两实现漂移）。"""
    raw = "|".join(
        [
            str(row.id),
            row.session_id,
            row.run_id,
            row.prev_hash,
            row.hit_type.value
            if hasattr(row.hit_type, "value")
            else str(row.hit_type),
            row.rule_id,
            row.judgement,
            row.excerpt,
            row.decision,
            row.trace_id,
            "1" if row.should_pass else "0" if row.should_pass is False else "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PgGuardHitStore:
    """guard_hit PG 存储：hash 链只追加，PG 失败抛异常（保守拒绝）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短连接，跨实例天然读回同一链）。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._dsn = _sync_dsn(dsn)

    def log_hit(
        self,
        session_id: str,
        run_id: str,
        hit: InputGuardHit,
        trace_id: str,
        should_pass: bool | None = None,
    ) -> int:
        """追加命中留证，返回自增 id；PG 失败抛 psycopg.Error（不放行）。

        事务内先取 advisory lock 串行化 append → SELECT 末行 → 算 hash → INSERT，
        保证并发命中不产生重复 id/断链。
        """
        decision = (
            hit.decision.value if hasattr(hit.decision, "value") else str(hit.decision)
        )
        with (
            psycopg.connect(self._dsn) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            # m1（v1.8 审查）：事务级 advisory lock 串行化 append——READ COMMITTED 下
            # 仅锁末行不足（并发事务看不到对方新插入的行，仍会算重 id 撞 PK 丢留证）。
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_KEY,))
            cur.execute(
                f"SELECT id, row_hash FROM {_TABLE} ORDER BY id DESC LIMIT 1"
            )
            last = cur.fetchone()
            row_id = int(last["id"]) + 1 if last else 1
            prev_hash = str(last["row_hash"]) if last else _GENESIS
            row = GuardHitRow(
                id=row_id,
                session_id=session_id,
                run_id=run_id,
                hit_type=hit.hit_type,
                rule_id=hit.rule_id,
                judgement=hit.judgement,
                excerpt=hit.excerpt,
                decision=decision,
                trace_id=trace_id,
                should_pass=should_pass,
                prev_hash=prev_hash,
                row_hash="",
            )
            row.row_hash = _chain_hash(row)
            cur.execute(
                f"INSERT INTO {_TABLE} "
                "(id, session_id, run_id, hit_type, rule_id, judgement, excerpt, "
                "stage, decision, trace_id, prev_hash, row_hash, should_pass) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row.id,
                    row.session_id,
                    row.run_id,
                    row.hit_type.value,
                    row.rule_id,
                    row.judgement,
                    row.excerpt,
                    "INPUT",
                    row.decision,
                    row.trace_id,
                    row.prev_hash,
                    row.row_hash,
                    row.should_pass,
                ),
            )
        return row_id

    @property
    def rows(self) -> list[GuardHitRow]:
        """从 PG 读回全部命中行（id 升序；与内存版 rows 接口一致）。"""
        with _connect_dict(self._dsn) as conn:
            raw = conn.execute(
                f"SELECT id, session_id, run_id, hit_type, rule_id, judgement, excerpt, "
                f"decision, trace_id, prev_hash, row_hash, should_pass "
                f"FROM {_TABLE} ORDER BY id"
            ).fetchall()
        return [
            GuardHitRow(
                id=int(r["id"]),
                session_id=str(r["session_id"]),
                run_id=str(r["run_id"]),
                hit_type=GuardHitType(str(r["hit_type"])),
                rule_id=str(r["rule_id"]),
                judgement=str(r["judgement"]),
                excerpt=str(r["excerpt"]),
                decision=str(r["decision"]),
                trace_id=str(r["trace_id"]),
                should_pass=r["should_pass"],
                prev_hash=str(r["prev_hash"]),
                row_hash=str(r["row_hash"]),
            )
            for r in raw
        ]

    def verify_chain(self) -> bool:
        """从 PG 读回全链校验：prev_hash 衔接 + row_hash 可复算。"""
        rows = self.rows
        for index, row in enumerate(rows):
            expected_prev = rows[index - 1].row_hash if index > 0 else _GENESIS
            if row.prev_hash != expected_prev or row.row_hash != _chain_hash(row):
                return False
        return True

    def false_positive_rate(self, labeled_should_pass: int) -> float:
        """从 PG 聚合 FPR = should_pass=True 数 / 标注应放行总数。"""
        if labeled_should_pass <= 0:
            return 0.0
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                f"SELECT count(*) FROM {_TABLE} WHERE should_pass = TRUE"
            ).fetchone()
        count = row[0] if row is not None else 0
        return int(count) / labeled_should_pass
