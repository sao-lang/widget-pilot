# M01 §3.3 L3 前端层：web dev 版（vite 热重载，full 模式使用）
# syntax=docker/dockerfile:1.7
FROM widgetpilot/node-base:dev
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
EXPOSE 5173
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD wget -qO- http://127.0.0.1:5173/ >/dev/null 2>&1 || exit 1
WORKDIR /app/apps/web
CMD ["pnpm", "exec", "vite", "--host", "0.0.0.0", "--port", "5173"]
