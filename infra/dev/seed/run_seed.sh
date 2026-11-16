#!/usr/bin/env bash
# seed 壳：加载 .env 并调用 run_seed.py；uv 缺失时回退系统 python。
set -uo pipefail
cd "$(dirname "$0")/../../.."

# 加载 .env（DATABASE_URL / ENV 等）
if [ -f .env ]; then
  set -a; source .env; set +a
fi

if command -v uv >/dev/null 2>&1; then
  uv run python infra/dev/seed/run_seed.py "$@"
else
  python infra/dev/seed/run_seed.py "$@"
fi
