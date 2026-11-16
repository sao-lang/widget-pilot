# M01 §3.3 L3 前端层：web prod 版（node 构建 → nginx 静态托管）
# full 开发模式走 web-dev.Dockerfile（vite 热重载）；本镜像用于产物构建验证与生产形态
# syntax=docker/dockerfile:1.7
FROM widgetpilot/node-base:dev AS builder
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/bff/package.json apps/bff/
COPY apps/web/package.json apps/web/
COPY packages/contracts/package.json packages/contracts/
COPY packages/sdk/package.json packages/sdk/
RUN --mount=type=cache,id=pnpm-store,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile --ignore-scripts
COPY apps/web/ apps/web/
COPY packages/ packages/
RUN pnpm --filter @widgetpilot/web exec tsc -b && pnpm --filter @widgetpilot/web exec vite build

FROM nginx:1.27-alpine AS runtime
COPY infra/docker/nginx-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/apps/web/dist /usr/share/nginx/html
USER 101
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1:80/healthz >/dev/null 2>&1 || exit 1
EXPOSE 80
