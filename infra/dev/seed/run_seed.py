#!/usr/bin/env python3
"""M01 §6.3 种子数据入口（seed 拥有，完整定义）。

种子集清单（§6.3）：
- accounts       : dev-user@widgetpilot.local（role=user）/ dev-curator@widgetpilot.local（role=curator）
- corpus_samples : 10 条脱敏公共语料样本（六种规格至少各 1 条）
- eval_fixtures  : 评测回放集 20 case（成功10/回边4/护栏3/预算2/沙箱失败1）
- demo_sessions  : 3 个示例会话（COMPLETED / FAILED / PAUSED）
- demo_events    : 3 会话的 agent_event / trace_span / cost_ledger
- objects        : checkpoint/ evidence/ spec/ code/ 前缀下带 #sha256: hash 的最小对象

约定（§6.3）：
- seed 启动即断言 ENV=dev，否则 exit 7；
- 幂等键 = 种子集名 + 实体自然键，重复执行 upsert 不产生重复行；
- 全部走应用层仓储函数写入（不走裸 SQL 直插业务表），保证 hash 链与 seq 连续性。
  当前业务仓储（M04–M11）尚未实现，故本入口在「schema 未就绪」时输出
  明确状态并以退出码 0 结束（环境搭建阶段不阻塞 dev-up），待各模块
  落地后由 run_seed.sh 自动切换到真实写入路径。

用法：uv run python infra/dev/seed/run_seed.py [--ci]
退出码：0 成功 / 2 配置缺失 / 7 非 dev 环境守卫 / 8 依赖不可达
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

# 脚本直跑（run_seed.sh）时项目根不在 sys.path，显式注入以导入应用层仓储
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from argon2 import PasswordHasher

from packages.contracts.enums import UserRole
from services.control_plane.accounts.pg_stores import PgUserStore
from services.control_plane.accounts.stores import (
    DuplicateEmailError,
    UserRow,
)

DEV_ACCOUNTS = [
    {"email": "dev-user@widgetpilot.local", "role": "user", "password": "Dev-123456"},
    {
        "email": "dev-curator@widgetpilot.local",
        "role": "curator",
        "password": "Curator-123456",
    },
]

GRID_SPECS = ["2x2", "2x4", "4x2", "4x4", "2x1", "1x2"]

# M02 §5.1：评测 fixture 按 ReasonCode 类别分布 10/4/3/2/1
EVAL_FIXTURE_DIST = {
    "ok": 10,
    "repair_loop": 4,
    "guard_hit": 3,
    "budget_exhausted": 2,
    "sandbox_failed": 1,
}


def fail(code: int, msg: str) -> None:
    print(f"SEED ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def check_env_guard() -> None:
    """§5.1：seed 只进 dev；ENV != dev → exit 7，零写入。"""
    env = os.getenv("ENV", "dev")
    if env != "dev":
        fail(7, f"seed 仅允许在 ENV=dev 执行，当前 ENV={env}（禁止向非 dev 栈播种）")


def check_config() -> None:
    if not os.getenv("DATABASE_URL"):
        fail(2, "DATABASE_URL 为空：请先 cp .env.example .env 并确认配置")


def seed_accounts(dsn: str) -> int:
    """把 DEV_ACCOUNTS 真实写入 PG users 表，返回本次新增行数（幂等）。

    幂等判据：email 已存在则跳过（D4），不重复播种、不覆盖已有用户密码。
    密码只存 argon2 hash（明文不入库、不进日志）。role 从种子清单读取，
    直接构造 UserRow 而非走 AuthService.register（后者不支持指定 curator）。

    Args:
        dsn: PostgreSQL 连接串。

    Returns:
        本次实际新增的账号数（已存在的不计），供 seed manifest 计数。
    """
    store = PgUserStore(dsn)
    hasher = PasswordHasher()
    created = 0
    for account in DEV_ACCOUNTS:
        email = account["email"].lower()
        if store.find_by_email(email) is not None:
            continue  # 幂等：已存在则跳过，不覆盖用户数据
        row = UserRow(
            id=str(uuid4()),
            email=email,
            password_hash=hasher.hash(account["password"]),
            role=UserRole(account["role"]),
        )
        try:
            store.insert(row)
        except DuplicateEmailError:
            continue  # 并发播种下另一实例已插入：折叠为幂等成功
        created += 1
    return created


async def seed_all(ci: bool) -> dict:
    """幂等播种。返回各种子集写入计数（幂等键 upsert，重复执行行数不变）。"""
    counts: dict[str, int] = {}
    dsn = os.getenv("DATABASE_URL", "")

    # ---- 1. accounts：真实写 PG（D3/D4；幂等 upsert + argon2 hash）----
    counts["accounts"] = seed_accounts(dsn)

    # ---- 2/3. 语料/评测/对象：仓储（M10/M19）未实现，保留声明式桩 ----
    # 这些计数是「目标种子集规模」，不代表已落库；真实写入待 M10/M19 仓储落地。
    counts["corpus_samples"] = 10
    counts["eval_fixtures"] = sum(EVAL_FIXTURE_DIST.values())

    # ---- 4/5. demo_sessions + demo_events（CI 最小集不含，§3.4 测试数据隔离）----
    if not ci:
        from packages.contracts.enums import AgentEventKind

        counts["demo_sessions"] = 3
        # 18 种 AgentEventKind（含 v1.3 ADR-001 的 ask_user）至少各出现 1 次
        kind_count = len(AgentEventKind)
        counts["demo_events"] = kind_count * 3
        counts["objects"] = 4  # checkpoint/ evidence/ spec/ code/ 各 1

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="WidgetPilot dev seed")
    parser.add_argument(
        "--ci", action="store_true", help="最小集：只账号 + fixture（§3.4）"
    )
    args = parser.parse_args()

    check_env_guard()
    check_config()

    counts = asyncio.run(seed_all(args.ci))
    manifest = {
        "seed_version": "v1.1",
        "ci": args.ci,
        "counts": counts,
        "written": ["accounts"],
        "stubbed": [
            "corpus_samples",
            "eval_fixtures",
            "demo_sessions",
            "demo_events",
            "objects",
        ],
        # 生成物完整性指纹（示例：seed 内容 hash，幂等性断言用）
        "content_hash": hashlib.sha256(
            json.dumps(counts, sort_keys=True).encode()
        ).hexdigest()[:16],
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("SEED OK（accounts 已真实写入 PG；其余子集为声明式桩，待 M10/M19 仓储落地）")
    sys.exit(0)


if __name__ == "__main__":
    main()
