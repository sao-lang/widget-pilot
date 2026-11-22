"""M06 guard_hit PG 留证集成测试（runbook §2 定稿，2026-11-21 补齐）。

验证真实 PG 落库 + prev_hash hash 链 + 跨实例读回 + FPR 复算——不是内存替身。
PG 不可用保守拒绝（不放行进模型）由单测 test_m06_guard_hit_pg_failclosed.py 覆盖
（用不可达 DSN，避免连接超时拖慢集成套件）。

前置：docker compose --profile core up -d；PG 不可达则 skip（不冒充通过）。
"""

from __future__ import annotations

import pytest

from services.guardrails.guard_hit_pg import PgGuardHitStore
from services.guardrails.types import GuardHitType, InputGuardHit
from tests.support.pg import pg_available, pg_connection, pg_execute

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"


def _infra_available() -> bool:
    """探测 PG 可达性；不可达则 skip。"""
    return pg_available(PG_DSN)


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_guard_hit() -> None:
    """每例前清空 guard_hit 表（集成测试隔离）。"""
    with pg_connection(PG_DSN) as conn:
        pg_execute(conn, "TRUNCATE TABLE guard_hit RESTART IDENTITY")


def _hit(rule_id: str = "INJ_IGNORE_RULES") -> InputGuardHit:
    """构造一条注入命中。"""
    return InputGuardHit(
        GuardHitType.INJECTION, rule_id, "注入规则命中", "忽略之前规则"
    )


class TestPgGuardHitStore:
    """guard_hit 真 PG 落库与 hash 链。"""

    def test_log_hit_persists_and_reads_back(self) -> None:
        """写入后新建 Store 实例能读回（落库非内存），四字段 + hash 齐全。"""
        store = PgGuardHitStore(PG_DSN)
        hit_id = store.log_hit("s1", "r1", _hit(), "t1")
        assert hit_id == 1

        fresh = PgGuardHitStore(PG_DSN)  # 新实例从库读
        rows = fresh.rows
        assert len(rows) == 1
        assert rows[0].session_id == "s1"
        assert rows[0].hit_type is GuardHitType.INJECTION
        assert rows[0].rule_id == "INJ_IGNORE_RULES"
        assert rows[0].excerpt == "忽略之前规则"
        assert len(rows[0].row_hash) == 64
        assert fresh.verify_chain() is True

    def test_hash_chain_links_rows(self) -> None:
        """第二行 prev_hash == 第一行 row_hash（链完整，新实例可校验）。"""
        store = PgGuardHitStore(PG_DSN)
        store.log_hit("s1", "r1", _hit("R1"), "t1")
        store.log_hit("s2", "r2", _hit("R2"), "t2")

        fresh = PgGuardHitStore(PG_DSN)
        rows = fresh.rows
        assert rows[1].prev_hash == rows[0].row_hash
        assert rows[0].prev_hash == "0" * 64  # 创世链
        assert fresh.verify_chain() is True

    def test_false_positive_rate_from_pg(self) -> None:
        """FPR 从 PG 的 should_pass 标注复算。"""
        store = PgGuardHitStore(PG_DSN)
        store.log_hit("s1", "r1", _hit(), "t1", should_pass=True)
        store.log_hit("s2", "r2", _hit(), "t2", should_pass=False)
        assert store.false_positive_rate(2) == 0.5
        assert PgGuardHitStore(PG_DSN).false_positive_rate(2) == 0.5  # 跨实例

    def test_concurrent_appends_no_lost_hit(self) -> None:
        """m1：并发追加不丢留证（advisory lock 串行化，两条都落库且链完整）。"""
        import concurrent.futures

        store = PgGuardHitStore(PG_DSN)

        def append(i: int) -> int:
            """并发追加一条命中。"""
            return store.log_hit(f"s{i}", f"r{i}", _hit(f"R{i}"), f"t{i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(append, range(2)))
        assert sorted(ids) == [1, 2]
        assert len(PgGuardHitStore(PG_DSN).rows) == 2
        assert PgGuardHitStore(PG_DSN).verify_chain() is True

    def test_asyncpg_dsn_normalized_for_production(self) -> None:
        """B1：compose 生产 DSN（postgresql+asyncpg://）归一化后仍可读写。"""
        asyncpg_dsn = PG_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
        store = PgGuardHitStore(asyncpg_dsn)  # 内部应归一化为同步 DSN
        store.log_hit("s-async", "r-async", _hit("ASYNC_DSN"), "t-async")
        assert any(r.session_id == "s-async" for r in PgGuardHitStore(PG_DSN).rows)

    def test_tamper_detected_by_verify_chain(self) -> None:
        """篡改 row_hash 后 verify_chain 返回 False（防篡改生效）。"""
        store = PgGuardHitStore(PG_DSN)
        store.log_hit("s1", "r1", _hit(), "t1")
        with pg_connection(PG_DSN) as conn:
            pg_execute(conn, "UPDATE guard_hit SET judgement = 'TAMPERED'")
        assert PgGuardHitStore(PG_DSN).verify_chain() is False
