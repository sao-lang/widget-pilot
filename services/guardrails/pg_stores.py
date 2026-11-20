"""M06 只追加日志的 PG 实现（M04 §9.0 接线义务 3）。

与 :mod:`services.guardrails.telemetry` 的内存 Store 同接口（append + 各自的
统计/归因方法），落 PG 后进程重启、新建实例仍可复算统计与归因链。只替换存储，
不重复判定逻辑（规则 8）。guard_hit 的 PG 实现在 guard_hit_pg.py（hash 链语义独立，不在此模块）。

表结构由迁移 ``0005_m06_telemetry`` 建立；Store 只负责读写，不建表。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Final, cast

import psycopg
from psycopg.rows import DictRow, dict_row

logger = logging.getLogger(__name__)

from services.guardrails.telemetry import (
    IntentRouteLogRow,
    NeedSkillLogRow,
    SkillPrefetchLogRow,
)
from services.guardrails.types import HitDecision, RouteLayer

# 表名集中一处（禁魔法字符串）：与迁移 0005_m06_telemetry 的表名逐字对齐。
_TABLE_INTENT_ROUTE: Final[str] = "intent_route_log"
_TABLE_SKILL_PREFETCH: Final[str] = "skill_prefetch_log"
_TABLE_NEED_SKILL: Final[str] = "need_skill_log"

# 统计口径键名（结构性字面量，与内存实现的返回契约一致）。
_KEY_ESCALATION_RATE: Final[str] = "escalation_rate"
_ASYNCPG_PREFIX: Final[str] = "postgresql+asyncpg://"


def _connect_dict(dsn: str) -> psycopg.Connection[DictRow]:
    """开返回 dict 行的同步连接（psycopg 类型桩未把 row_factory 纳入泛型推断）。"""
    # 反射边界豁免：dict_row 由 psycopg 运行时注入行工厂，类型桩无法表达其行类型，
    # 故在此一处 cast(Any, dict_row) 收口，下游只用 DictRow 具名视图。
    return cast(
        psycopg.Connection[DictRow],
        psycopg.connect(dsn, row_factory=cast(Any, dict_row)),
    )


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


class PgIntentRouteLogStore:
    """IntentRouteLog 的 PG 只追加存储：stats() 从库内记录复算各级占比。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短连接，跨实例天然可读回）。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._dsn: str = _sync_dsn(dsn)

    def append(self, row: IntentRouteLogRow) -> None:
        """追加一条意图裁定记录到 intent_route_log。

        Args:
            row: 裁定留证 Row（字段逐项落列）。
        """
        sql = (
            f"INSERT INTO {_TABLE_INTENT_ROUTE} "
            "(session_id, run_id, route_layer, intent, similarity, latency_ms, "
            "samples_version, trace_id, fallback_reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        # M2（v1.8）：留证写入失败须隔离——PG 抖动不能击穿校验链把正常业务打挂
        # （否则 dispatcher 不 ACK→重试→死信）。只告警，不影响 ValidationResult。
        try:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    sql,
                    (
                        row.session_id,
                        row.run_id,
                        row.route_layer.value,
                        row.intent,
                        row.similarity,
                        row.latency_ms,
                        row.samples_version,
                        row.trace_id,
                        row.fallback_reason,
                    ),
                )
        except psycopg.Error as exc:
            logger.warning("intent_route_log append failed (evidence dropped): %s", exc)

    def stats(self) -> dict[str, float]:
        """从 PG 复算各级占比与升级率（L3+L4 占比），口径同内存实现。"""
        with _connect_dict(self._dsn) as conn:
            rows = conn.execute(
                f"SELECT route_layer FROM {_TABLE_INTENT_ROUTE}"
            ).fetchall()
        total = len(rows)
        if total == 0:
            return {layer.value: 0.0 for layer in RouteLayer} | {
                _KEY_ESCALATION_RATE: 0.0
            }
        counts: Counter[RouteLayer] = Counter(
            RouteLayer(str(row["route_layer"])) for row in rows
        )
        result = {layer.value: counts[layer] / total for layer in RouteLayer}
        result[_KEY_ESCALATION_RATE] = (
            counts[RouteLayer.L3] + counts[RouteLayer.L4]
        ) / total
        return result


class PgSkillPrefetchLogStore:
    """SkillPrefetchLog 的 PG 只追加存储：misses() 从库内记录复算漏加载归因。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._dsn: str = _sync_dsn(dsn)

    def append(self, row: SkillPrefetchLogRow) -> None:
        """追加一条预取记录到 skill_prefetch_log（recipe_ids 以 JSON 文本往返）。

        Args:
            row: 预取留证 Row。
        """
        sql = (
            f"INSERT INTO {_TABLE_SKILL_PREFETCH} "
            "(session_id, run_id, widget_type, grid, recipe_ids, hit, reason, trace_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        # M2（v1.8）：留证失败隔离（同 intent_route_log，PG 抖动不击穿校验链）。
        try:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    sql,
                    (
                        row.session_id,
                        row.run_id,
                        row.widget_type,
                        row.grid,
                        json.dumps(list(row.recipe_ids)),
                        row.hit,
                        row.reason,
                        row.trace_id,
                    ),
                )
        except psycopg.Error as exc:
            logger.warning("skill_prefetch_log append failed (evidence dropped): %s", exc)

    def misses(self) -> tuple[SkillPrefetchLogRow, ...]:
        """返回库内所有未命中记录（每条 reason 非空，供漏加载归因）。"""
        with _connect_dict(self._dsn) as conn:
            rows = conn.execute(
                f"SELECT session_id, run_id, widget_type, grid, recipe_ids, "
                f"reason, trace_id FROM {_TABLE_SKILL_PREFETCH} "
                "WHERE hit = FALSE ORDER BY id"
            ).fetchall()
        return tuple(
            SkillPrefetchLogRow(
                session_id=str(row["session_id"]),
                run_id=str(row["run_id"]),
                widget_type=str(row["widget_type"]),
                grid=str(row["grid"]),
                recipe_ids=tuple(json.loads(str(row["recipe_ids"]))),
                hit=False,
                reason=(str(row["reason"]) if row["reason"] is not None else None),
                trace_id=str(row["trace_id"]),
            )
            for row in rows
        )


class PgNeedSkillLogStore:
    """NeedSkillLog 的 PG 只追加存储：decisions() 从库内记录复算决策计数。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._dsn: str = _sync_dsn(dsn)

    def append(self, row: NeedSkillLogRow) -> None:
        """追加一条 need_skill 决策记录到 need_skill_log。

        Args:
            row: 决策留证 Row；decision 为 None 时落 NULL。
        """
        sql = (
            f"INSERT INTO {_TABLE_NEED_SKILL} "
            "(session_id, run_id, recipe_id, depth, turn_count, tokens, "
            "decision, reason, trace_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        # M2（v1.8）：留证失败隔离（同上，PG 抖动不击穿校验链）。
        try:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    sql,
                    (
                        row.session_id,
                        row.run_id,
                        row.recipe_id,
                        row.depth,
                        row.turn_count,
                        row.tokens,
                        row.decision.value if row.decision is not None else None,
                        row.reason,
                        row.trace_id,
                    ),
                )
        except psycopg.Error as exc:
            logger.warning("need_skill_log append failed (evidence dropped): %s", exc)

    def decisions(self) -> Counter[HitDecision | None]:
        """按 decision 计数（含 None），口径同内存实现。"""
        with _connect_dict(self._dsn) as conn:
            rows = conn.execute(
                f"SELECT decision FROM {_TABLE_NEED_SKILL}"
            ).fetchall()
        return Counter(
            HitDecision(str(row["decision"])) if row["decision"] is not None else None
            for row in rows
        )
