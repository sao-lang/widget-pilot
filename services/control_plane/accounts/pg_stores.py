"""M04 PG 版账号存储（UserStore Protocol 的生产实现，§6.1 users 表）。

v1.5 ORM 化：手写 SQL → SQLAlchemy ORM（models.User + db.session_scope）。
实现 UserStore 协议，email 唯一性由 PG UNIQUE 约束保证，
冲突映射为 DuplicateEmailError（register 409）。
"""

from __future__ import annotations

import psycopg
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from services.control_plane.accounts.stores import (
    DuplicateEmailError,
    UserRow,
)
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import User


class PgUserStore:
    """PG 用户表：实现 UserStore Protocol（§6.1 users 表，ORM 实现）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN，构造该实例绑定的 sessionmaker。

        Args:
            dsn: PostgreSQL 连接串（asyncpg 前缀内部转 psycopg 同步）。
        """
        self._factory = make_session_factory(dsn)

    def insert(self, row: UserRow) -> None:
        """插入用户；email 冲突映射为 DuplicateEmailError。

        Args:
            row: 用户行（id 服务端生成，password_hash 已是哈希值，email 转小写存）。

        Raises:
            DuplicateEmailError: email 已存在（对应注册 409）。
        """
        stmt = insert(
            User
        ).values(
            id=row.id,
            email=row.email.lower(),  # 应用层 lower() 保证大小写不敏感（迁移为 Text+unique）
            password_hash=row.password_hash,
            role=row.role.value,
            share_enabled=row.share_enabled,
            quota=row.quota,  # JSON 类型直接传 dict，由 SQLAlchemy 序列化
            created_at=row.created_at,
        )
        try:
            with session_scope(self._factory) as session:
                session.execute(stmt)
        except IntegrityError as exc:
            # 行为等价：原实现把所有 UniqueViolation（email 与主键）统一映射为
            # DuplicateEmailError；此处不做约束名硬编码（schema 改名即失效），
            # 完全复刻旧语义。用户不存在/密码错误的折叠在 service 层，与此无关。
            if isinstance(exc.orig, psycopg.errors.UniqueViolation):
                raise DuplicateEmailError(row.email) from exc
            raise

    def find_by_email(self, email: str) -> UserRow | None:
        """按 email（小写）查用户，无则返回 None。

        Args:
            email: 登录名；转小写后查（大小写不敏感）。
        """
        with session_scope(self._factory) as session:
            obj = session.scalar(select(User).where(User.email == email.lower()))
        return obj.to_row() if obj is not None else None

    def find_by_id(self, user_id: str) -> UserRow | None:
        """按 id 查用户，无则返回 None。

        Args:
            user_id: 用户标识（JWT sub claims）；refresh 时据此取 DB 最新角色。
        """
        with session_scope(self._factory) as session:
            obj = session.scalar(select(User).where(User.id == user_id))
        return obj.to_row() if obj is not None else None
