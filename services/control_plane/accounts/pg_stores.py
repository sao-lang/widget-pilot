"""M04 PG 版账号存储（UserStore Protocol 的生产实现，§6.1 users 表）。

与 InMemoryUserStore 同协议，供 AuthService 无差别注入（规则 8/9：
协议 + 默认/生产双实现，装配期由 main.py 选择）。email 唯一性由 PG
UNIQUE 约束保证，冲突映射为 DuplicateEmailError（register 409）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import psycopg

from packages.contracts.enums import UserRole
from services.control_plane.accounts.stores import (
    DuplicateEmailError,
    UserRow,
)


class PgUserStore:
    """PG 用户表：实现 UserStore Protocol（§6.1 users 表）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN；每操作用短连接（生产可换连接池，本模块保持简单）。"""
        self._dsn = dsn

    def insert(self, row: UserRow) -> None:
        """插入用户；email 冲突（PG UniqueViolation）映射为 DuplicateEmailError。

        Args:
            row: 用户行（id 服务端生成，password_hash 已是哈希值，email 转小写存）。

        Raises:
            DuplicateEmailError: email 已存在（对应注册 409）。
        """
        try:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, email, password_hash, role, share_enabled, quota, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        row.id,
                        row.email.lower(),  # citext 语义：统一小写存储
                        row.password_hash,
                        row.role.value,
                        row.share_enabled,
                        json.dumps(row.quota),
                        row.created_at,
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateEmailError(row.email) from exc

    def find_by_email(self, email: str) -> UserRow | None:
        """按 email（小写）查用户，无则返回 None。

        Args:
            email: 登录名；转小写后查（citext 语义，大小写不敏感）。
        """
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, password_hash, role, share_enabled, quota, created_at "
                "FROM users WHERE email = %s",
                (email.lower(),),
            )
            row = cur.fetchone()
        return _to_user_row(row) if row else None

    def find_by_id(self, user_id: str) -> UserRow | None:
        """按 id 查用户，无则返回 None。

        Args:
            user_id: 用户标识（JWT sub claims）；refresh 时据此取 DB 最新角色。
        """
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, password_hash, role, share_enabled, quota, created_at "
                "FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        return _to_user_row(row) if row else None


def _to_user_row(row: tuple[object, ...]) -> UserRow:
    """PG 行 → UserRow（动态边界单点收窄，编码规则 6）。

    未知 role 值显式回退 user（规则 5：未知值回退安全成员）；quota 为 JSONB，
    非 dict 形态（异常数据）回退空字典，不让脏数据污染上游。
    """
    raw_role = str(row[3])
    try:
        role = UserRole(raw_role)
    except ValueError:
        role = UserRole.USER
    raw_quota = row[5]
    quota = (
        raw_quota if isinstance(raw_quota, dict) else json.loads(str(raw_quota or "{}"))
    )
    return UserRow(
        id=str(row[0]),
        email=str(row[1]),
        password_hash=str(row[2]),
        role=role,
        share_enabled=bool(row[4]),
        quota=quota,
        created_at=row[6] if isinstance(row[6], datetime) else datetime.now(UTC),
    )
