# M01 §3.3 L3 BFF 层：apps/bff（SSO / 首屏聚合 / 短期 SSE ticket；不做鉴权判定）
# syntax=docker/dockerfile:1.7
FROM widgetpilot/node-base:dev
WORKDIR /app
# workspace 元数据 → 各包 package.json（锁文件层）
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/bff/package.json apps/bff/
COPY apps/web/package.json apps/web/
COPY packages/contracts/package.json packages/contracts/
COPY packages/sdk/package.json packages/sdk/
RUN --mount=type=cache,id=pnpm-store,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile --ignore-scripts
# 源码层
COPY apps/bff/ apps/bff/
COPY packages/ packages/
EXPOSE 8010
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8010/api/bff/health >/dev/null 2>&1 || exit 1
WORKDIR /app/apps/bff
CMD ["pnpm", "exec", "tsx", "server.ts"]
