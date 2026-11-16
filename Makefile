# ============================================================
# WidgetPilot 开发工作流（M01 §1.2 R5 / §4.2 目标契约 / §5.1 退出码）
# 约定：全部目标可脚本化（无交互阻塞）；失败即非零退出码；
#       破坏性操作必须 CONFIRM=dev-reset 显式确认。
# Windows 无 make 时：bash infra/dev/dev.sh <target>（等价入口）
# ============================================================

COMPOSE = docker compose -f docker-compose.dev.yml
PROFILES ?= core
SVC ?=
SEED_ARGS ?=

# ---- 退出码（§5.1）：2 配置缺失 / 3 端口冲突 / 4 依赖不健康 / 5 未确认 / 6 docker 不可用 / 7 seed 守卫 / 8 迁移失败 ----

.PHONY: dev-up dev-down dev-reset seed migrate logs ps port-check sandbox-build build-images

# 分层镜像构建（L1 基座 → L2/L3 服务层）：先基座后服务
# python-base / node-base 一次构建全服务共享；服务镜像只含源码层
build-images: docker-check
	@set -euo pipefail; \
	docker build -t widgetpilot/python-base:dev -f infra/docker/python-base.Dockerfile . ; \
	docker build -t widgetpilot/node-base:dev -f infra/docker/node-base.Dockerfile . ; \
	$(COMPOSE) --profile full build ; \
	echo "images built: python-base, node-base, 7 services, web-dev, bff"

# Docker 可用性前置探测（失败 exit 6）
docker-check:
	@docker version --format '{{.Server.Version}}' >/dev/null 2>&1 || { \
	  echo "ERROR: Docker daemon 不可用。请启动 Docker Desktop 并确认 WSL2 backend 已启用（Settings → General → Use the WSL 2 based engine）"; exit 6; }

# .env 配置守卫（D16：缺失 exit 2 并提示复制 .env.example）
env-check:
	@if [ ! -f .env ]; then echo "ERROR: .env 不存在。请先执行: cp .env.example .env"; exit 2; fi

# 端口冲突前置检测（§3.5，冲突 exit 3）
port-check: docker-check
	@bash infra/dev/port_check.sh

# 起栈：port_check → compose up --wait（healthcheck 全绿，--wait 超时 120s exit 4）→ migrate → 端点清单
dev-up: env-check docker-check port-check
	@set -euo pipefail; \
	$(COMPOSE) --profile $(PROFILES) up -d --wait --wait-timeout 120 || { \
	  echo "ERROR: 依赖栈未在 120s 内全绿（exit 4）。排查: make logs / make ps"; exit 4; }; \
	$(MAKE) migrate || exit 8; \
	echo ""; echo "=== WidgetPilot dev 依赖栈 READY ==="; \
	echo "  postgres : postgresql://$(shell grep -oP '(?<=POSTGRES_USER=)[^ ]+' .env 2>/dev/null || echo widgetpilot):***@127.0.0.1:$$(grep -oP '(?<=POSTGRES_PORT=)[0-9]+' .env 2>/dev/null || echo 5432)"; \
	echo "  redis    : redis://127.0.0.1:6379/0"; \
	echo "  minio    : http://127.0.0.1:9000 (API) / http://127.0.0.1:9001 (Console)"; \
	echo "  otel     : http://127.0.0.1:4317 (gRPC) / 4318 (HTTP)   [observability profile 时]"; \
	echo "  grafana  : http://127.0.0.1:3000                        [observability profile 时]"; \
	echo "本机起应用: uv run uvicorn services.control_plane.api.main:app --reload"; \
	echo "前端     : cd apps/web && pnpm dev"

# 停栈：停容器与 network，保留全部 named volume（数据不丢）
dev-down:
	$(COMPOSE) down

# 破坏性重置：必须 CONFIRM=dev-reset（否则 exit 5，零副作用，D6）
dev-reset:
	@if [ "$(CONFIRM)" != "dev-reset" ]; then \
	  echo "ERROR: 破坏性操作。将清空 pgdata/redis-data/minio-data/loki-data，数据不可恢复。"; \
	  echo "确认执行请运行: CONFIRM=dev-reset make dev-reset"; exit 5; fi
	@$(COMPOSE) down -v --remove-orphans
	@$(MAKE) dev-up
	@$(MAKE) seed
	@echo "dev stack reset to factory state."

# 播种：幂等 upsert；仅 ENV=dev 可执行（非 dev exit 7，§5.1）
seed: env-check docker-check
	@bash infra/dev/seed/run_seed.sh $(SEED_ARGS)

# schema 迁移：alembic upgrade head（已 head 则 no-op，D14；失败 exit 8）
migrate: env-check docker-check
	@PYTHONUTF8=1 uv run alembic -c services/control_plane/migrations/alembic.ini upgrade head \
	  || { echo "ERROR: 迁移失败（exit 8）"; exit 8; }

# 日志：make logs（全部）/ make logs SVC=postgres
logs:
	@if [ -n "$(SVC)" ]; then $(COMPOSE) logs --tail=100 -f $(SVC); else $(COMPOSE) logs --tail=100 -f; fi

# 服务 / 状态 / 端口表
ps:
	$(COMPOSE) ps

# 沙箱镜像构建透传入口（本体归 M08 infra/sandbox；本模块只透传，§4.2）
sandbox-build:
	@if [ ! -d infra/sandbox ]; then \
	  echo "ERROR: infra/sandbox 不存在——沙箱镜像定义归 M08，本模块不代建。"; exit 1; fi
	docker build -t wp-sandbox:dev -f infra/sandbox/Dockerfile infra/sandbox
