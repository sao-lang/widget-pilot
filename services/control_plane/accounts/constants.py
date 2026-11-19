"""M04 认证与租约常量（§2.1 固化表，v1.4）。

所有值来自设计定稿：JWT TTL 为【契约约定】，租约/心跳/SSE 窗口为 M02 Q4
定稿值（影子校准中）。这些是代码常量，无在线关闭开关（§2.1 硬约束）。
密钥不在此定义——从环境注入，禁止入库与写 overview。
"""

from __future__ import annotations

ACCESS_TOKEN_TTL_S: int = 900
"""短时访问令牌有效期：15 分钟（§2 认证契约约定）。"""

REFRESH_TOKEN_TTL_S: int = 604800
"""刷新令牌有效期：7 天（§2 认证契约约定）。"""

JWT_ALGORITHM: str = "HS256"
"""JWT 签名算法（§2.1 固化表）。"""

LEASE_TTL_S: int = 30
"""租约 TTL（M02 Q4 定稿，影子校准中）。"""

HEARTBEAT_INTERVAL_S: int = 10
"""租约心跳间隔（M02 Q4 定稿，影子校准中）。"""

SSE_WINDOW_N: int = 500
"""SSE 保留窗口，超窗先拉快照（M02 Q4 定稿，影子校准中）。"""

TOKEN_TYPE_ACCESS: str = "access"
"""access 令牌类型声明（区分 refresh，防混用）。"""

TOKEN_TYPE_REFRESH: str = "refresh"
"""refresh 令牌类型声明。"""
