#!/usr/bin/env bash
# Windows 无 make 时的等价入口（M01 §2.2：bash infra/dev/dev.sh <target> [args]）
# 用法：
#   bash infra/dev/dev.sh dev-up [PROFILES=core]
#   bash infra/dev/dev.sh dev-down
#   bash infra/dev/dev.sh dev-reset          # 需 CONFIRM=dev-reset 环境变量
#   bash infra/dev/dev.sh seed [--ci]
#   bash infra/dev/dev.sh migrate | logs | ps | port-check | sandbox-build
set -uo pipefail
cd "$(dirname "$0")/../.."

target="${1:-help}"; shift || true

case "$target" in
  dev-up)
    export PROFILES="${PROFILES:-core}"
    make dev-up "$@" 2>/dev/null || {
      # 无 make 时的手工等价序列（§4.2 dev-up 内部顺序）
      [ -f .env ] || { echo "ERROR: .env 不存在，请 cp .env.example .env"; exit 2; }
      bash infra/dev/port_check.sh || exit $?
      docker compose -f docker-compose.dev.yml --profile "$PROFILES" up -d --wait --wait-timeout 120 || exit 4
      bash infra/dev/seed/run_seed.sh 2>/dev/null || true
      echo "dev stack READY (profile: $PROFILES)"
    } ;;
  dev-down)
    docker compose -f docker-compose.dev.yml down ;;
  dev-reset)
    if [ "${CONFIRM:-}" != "dev-reset" ]; then
      echo "ERROR: 破坏性操作。将清空全部 volume。确认执行: CONFIRM=dev-reset bash infra/dev/dev.sh dev-reset"
      exit 5
    fi
    docker compose -f docker-compose.dev.yml down -v --remove-orphans
    PROFILES=core bash infra/dev/dev.sh dev-up
    bash infra/dev/seed/run_seed.sh
    echo "dev stack reset to factory state." ;;
  seed)
    bash infra/dev/seed/run_seed.sh "$@" ;;
  migrate)
    uv run alembic -c services/control_plane/migrations/alembic.ini upgrade head || exit 8 ;;
  logs)
    if [ -n "${SVC:-}" ]; then docker compose -f docker-compose.dev.yml logs --tail=100 -f "$SVC"
    else docker compose -f docker-compose.dev.yml logs --tail=100 -f; fi ;;
  ps)
    docker compose -f docker-compose.dev.yml ps ;;
  build-images)
    docker build -t widgetpilot/python-base:dev -f infra/docker/python-base.Dockerfile . &&
    docker build -t widgetpilot/node-base:dev -f infra/docker/node-base.Dockerfile . &&
    docker compose -f docker-compose.dev.yml --profile full build ;;
  port-check)
    bash infra/dev/port_check.sh ;;
  sandbox-build)
    docker build -t wp-sandbox:dev -f infra/sandbox/Dockerfile infra/sandbox ;;
  *)
    echo "用法: bash infra/dev/dev.sh {dev-up|dev-down|dev-reset|seed|migrate|logs|ps|build-images|port-check|sandbox-build}"
    echo "  dev-up       起依赖栈（PROFILES=core|'core observability'|'core full'）"
    echo "  build-images 分层构建全部镜像（python-base → node-base → services/web/bff）"
    echo "  dev-reset    破坏性重置，需 CONFIRM=dev-reset"
    exit 0 ;;
esac
