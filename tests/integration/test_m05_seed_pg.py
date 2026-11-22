"""M01 seed 账号真实写库集成测试（D3/D4：幂等 + 可登录）。

补充纯打印桩无法覆盖的部分：账号真落 users 表、argon2 hash 可登录、
重复执行不产生重复行；其余子集（语料/评测/对象）仓储未实现，明确为桩。

前置：docker compose --profile core up -d（PG:5432）；
本机无容器时整文件 skip（连通性守卫），不冒充通过。
"""

from __future__ import annotations

import pytest

from infra.dev.seed.run_seed import seed_accounts
from packages.contracts.enums import UserRole
from services.control_plane.accounts.pg_stores import PgUserStore
from services.control_plane.accounts.service import AuthError, AuthService
from tests.support.pg import pg_available, pg_connection, pg_execute, pg_scalar

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
SEED_SECRET = "seed-only-secret-not-for-production"


def _infra_available() -> bool:
    """探测 PG 是否可达；不可达则跳过（不冒充通过）。"""
    return pg_available(PG_DSN)


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_users() -> None:
    """每例前清空 users 表（集成测试隔离）。"""
    with pg_connection(PG_DSN) as conn:
        pg_execute(conn, "TRUNCATE TABLE users CASCADE")


class TestSeedAccounts:
    """D3/D4：账号真实写 PG + 幂等 + 可登录。"""

    def test_seed_writes_two_accounts_to_pg(self) -> None:
        """首次播种：两个 dev 账号真落库，roles 正确。"""
        counts = seed_accounts(PG_DSN)
        assert counts == 2
        store = PgUserStore(PG_DSN)
        user = store.find_by_email("dev-user@widgetpilot.local")
        curator = store.find_by_email("dev-curator@widgetpilot.local")
        assert user is not None and user.role is UserRole.USER
        assert curator is not None and curator.role is UserRole.CURATOR

    def test_seed_is_idempotent(self) -> None:
        """重复播种不新增行，第二次返回 0。"""
        assert seed_accounts(PG_DSN) == 2
        assert seed_accounts(PG_DSN) == 0
        with pg_connection(PG_DSN) as conn:
            assert pg_scalar(conn, "SELECT COUNT(*) FROM users") == 2

    def test_seeded_account_can_login(self) -> None:
        """argon2 hash 真实可校验：播后的账号能用明文密码登录。"""
        seed_accounts(PG_DSN)
        auth = AuthService(PgUserStore(PG_DSN), SEED_SECRET)
        result = auth.login("dev-user@widgetpilot.local", "Dev-123456")
        assert result.user.role is UserRole.USER
        assert result.access_token
        with pytest.raises(AuthError):
            auth.login("dev-user@widgetpilot.local", "wrong-password")
