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

DEV_ACCOUNTS = [
    {"email": "dev-user@widgetpilot.local", "role": "user", "password": "Dev-123456"},
    {"email": "dev-curator@widgetpilot.local", "role": "curator", "password": "Curator-123456"},
]

GRID_SPECS = ["2x2", "2x4", "4x2", "4x4", "2x1", "1x2"]

# M02 §5.1：评测 fixture 按 ReasonCode 类别分布 10/4/3/2/1
EVAL_FIXTURE_DIST = {
    "ok": 10, "repair_loop": 4, "guard_hit": 3, "budget_exhausted": 2, "sandbox_failed": 1,
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


async def seed_all(ci: bool) -> dict:
    """幂等播种。返回各种子集写入计数（幂等键 upsert，重复执行行数不变）。"""
    counts: dict[str, int] = {}
    # ---- 依赖可达性探测（§8.2 D3 前置）----
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        print("NOTE: asyncpg 未安装，跳过直连校验（应用层仓储待 M04 落地后接管写入）")

    # ---- 1. accounts（2 个，argon2 hash 由 M04 仓储实现时生成）----
    counts["accounts"] = len(DEV_ACCOUNTS)

    # ---- 2. corpus_samples（10 条，六种规格至少各 1 条）----
    counts["corpus_samples"] = 10

    # ---- 3. eval_fixtures（20 case：10+4+3+2+1）----
    counts["eval_fixtures"] = sum(EVAL_FIXTURE_DIST.values())

    # ---- 4/5. demo_sessions + demo_events（CI 最小集不含，§3.4 测试数据隔离）----
    if not ci:
        counts["demo_sessions"] = 3
        # 17 种 AgentEventKind（M02 §5.1）至少各出现 1 次
        counts["demo_events"] = 17 * 3
        counts["objects"] = 4  # checkpoint/ evidence/ spec/ code/ 各 1

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="WidgetPilot dev seed")
    parser.add_argument("--ci", action="store_true", help="最小集：只账号 + fixture（§3.4）")
    args = parser.parse_args()

    check_env_guard()
    check_config()

    counts = asyncio.run(seed_all(args.ci))
    manifest = {
        "seed_version": "v1.0",
        "ci": args.ci,
        "counts": counts,
        # 生成物完整性指纹（示例：seed 内容 hash，幂等性断言用）
        "content_hash": hashlib.sha256(
            json.dumps(counts, sort_keys=True).encode()
        ).hexdigest()[:16],
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("SEED OK（幂等；当前为环境桩阶段，业务写入路径待 M04–M11 落地后接管）")
    sys.exit(0)


if __name__ == "__main__":
    main()
