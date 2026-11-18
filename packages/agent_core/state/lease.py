"""M03 租约抢锁 / 心跳 / 恢复（§11.4 / §4.1 L16-L19）。

库内定义算法；LeaseProvider 由服务层注入（§4.3）。同时暴露对象式（run 用）
与模块级函数式（恢复扫描用）两种调用形态（§4.1 说明）。原子抢锁语义由
Provider 保证（UPDATE ... WHERE lease_until < now()）。
"""
from __future__ import annotations

from datetime import datetime

from packages.agent_core.injection import Lease, LeaseProvider


def acquire(provider: LeaseProvider, run_id: str, owner: str, ttl_s: int) -> Lease:
    """模块级抢租约；同一 runId 只有一个持锁者（T19）。"""
    return provider.acquire(run_id, owner, ttl_s)


def heartbeat(provider: LeaseProvider, run_id: str, owner: str) -> bool:
    """续租：仅当 owner 仍持锁时刷新到期时间，返回是否续约成功。"""
    return provider.heartbeat(run_id, owner)


def release(provider: LeaseProvider, run_id: str, owner: str) -> None:
    """释放租约：仅 owner 可主动归还，供正常结束时立即让出持锁权。"""
    provider.release(run_id, owner)


def scan_expired(provider: LeaseProvider, now: datetime) -> list[str]:
    """扫描 lease_until < now AND terminal IS NULL 的 runId（§11.4 step 3）。"""
    return provider.scan_expired(now)
