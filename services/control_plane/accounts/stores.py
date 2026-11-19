"""M04 用户存储（§6.1 User 实体的持久化抽象）。

生产实现为 PG（alembic 迁移建表）；内存实现供 §8.1 无 Docker 环境跑全用例。
两者都按 email 唯一索引约束，租户隔离键为 user.id。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from packages.contracts.enums import UserRole


@dataclass(slots=True)
class UserRow:
    """User 表的一行（§6.1 字段：id/email/password_hash/role/share_enabled/quota/created_at）。"""

    id: str
    """账号标识（uuid，服务端生成）。"""
    email: str
    """登录名（citext，唯一）。"""
    password_hash: str
    """ argon2/bcrypt 哈希，只存 hash 不存明文。"""
    role: UserRole = UserRole.USER
    """两角色（D-3）：user / curator。"""
    share_enabled: bool = True
    """是否允许产物进公共语料（D-1）。"""
    quota: dict[str, int] = field(default_factory=dict)
    """配额归属（jsonb，M11 消费）。"""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """创建时间（UTC）。"""


class DuplicateEmailError(Exception):
    """email 唯一索引冲突（对应 register 409）。"""


@runtime_checkable
class UserStore(Protocol):
    """用户存储协议（编码规则 8/9：服务依赖抽象，PG/内存双实现可插拔）。"""

    def insert(self, row: UserRow) -> None:
        """插入用户；email 重复抛 DuplicateEmailError。"""
        ...

    def find_by_email(self, email: str) -> UserRow | None:
        """按 email（大小写不敏感）查用户。"""
        ...

    def find_by_id(self, user_id: str) -> UserRow | None:
        """按 id 查用户（供 JWT claims 还原）。"""
        ...


class InMemoryUserStore:
    """内存用户表：按 email 与 id 双索引，唯一性由注册路径保证。"""

    def __init__(self) -> None:
        """初始化空表与双索引。"""
        self._by_email: dict[str, UserRow] = {}
        self._by_id: dict[str, UserRow] = {}

    def insert(self, row: UserRow) -> None:
        """插入用户；email 重复抛 DuplicateEmailError（对齐 PG UNIQUE 约束）。"""
        if row.email in self._by_email:
            raise DuplicateEmailError(row.email)
        self._by_email[row.email] = row
        self._by_id[row.id] = row

    def find_by_email(self, email: str) -> UserRow | None:
        """按 email（大小写不敏感，模拟 citext）查用户，无则返回 None。"""
        return self._by_email.get(email.lower())

    def find_by_id(self, user_id: str) -> UserRow | None:
        """按 id 查用户，无则返回 None（供 JWT claims → 用户还原）。"""
        return self._by_id.get(user_id)
