#!/usr/bin/env bash
# M01 §2.3：依赖健康等待兜底（CI 用）。compose --wait 失败时的重试探针。
set -uo pipefail
TIMEOUT="${1:-120}"
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  ok=1
  docker exec widgetpilot-dev-postgres-1 pg_isready -U widgetpilot -d widgetpilot >/dev/null 2>&1 || ok=0
  docker exec widgetpilot-dev-redis-1 redis-cli ping 2>/dev/null | grep -q PONG || ok=0
  [ "$ok" -eq 1 ] && { echo "deps healthy"; exit 0; }
  sleep 2
done
echo "ERROR: deps not healthy within ${TIMEOUT}s"; exit 4
