#!/usr/bin/env bash
# M01 §3.5 / §4.2：起栈前端口占用前置检测。冲突 exit 3，compose 不被拉起。
# 需检测端口来自 .env（缺失走默认值）。Windows 下额外提示保留端口段检查。
set -uo pipefail

env_val() {  # 读 .env 中某 key，缺失回退默认
  local key="$1" def="$2"
  if [ -f .env ]; then
    local v
    v=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2- | tr -d '[:space:]')
    [ -n "$v" ] && echo "$v" && return
  fi
  echo "$def"
}

PORTS=(
  "$(env_val POSTGRES_PORT 5432)"
  "6379"
  "9000"
  "9001"
)

# observability / full profile 下的附加端口（从 COMPOSE_PROFILES 判断）
PROFILES="$(env_val COMPOSE_PROFILES core)"
case "$PROFILES" in
  *observability*)
    PORTS+=("4317" "4318" "3100" "$(env_val GRAFANA_PORT 3000)") ;;
esac
case "$PROFILES" in
  *full*)
    PORTS+=("8000" "8100" "8200" "8300" "5173") ;;
esac

conflict=0
for p in "${PORTS[@]}"; do
  if command -v powershell.exe >/dev/null 2>&1; then
    # Windows：netstat 探测监听
    if powershell.exe -NoProfile -Command "Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1" | grep -q LocalPort; then
      echo "PORT CONFLICT: $p 已被本机进程监听"; conflict=1
    fi
  elif command -v ss >/dev/null 2>&1; then
    if ss -ltn "sport = :$p" | grep -q LISTEN; then
      echo "PORT CONFLICT: $p 已被本机进程监听"; conflict=1
    fi
  elif command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "PORT CONFLICT: $p 已被本机进程监听"; conflict=1
    fi
  fi
done

if [ "$conflict" -ne 0 ]; then
  echo ""
  echo "Windows 提示：若端口未被进程占用但仍冲突，检查 Hyper-V 系统保留段："
  echo "  netsh int ipv4 show excludedportrange protocol=tcp"
  echo "可在 .env 中覆写对应端口（如 GRAFANA_PORT）后重试。"
  exit 3
fi
echo "port-check OK: ${PORTS[*]}"
exit 0
