# M08 一次性沙箱镜像：node + esbuild + jsdom + React。
# 构建：make sandbox-build（Makefile 已透传 -f infra/sandbox/Dockerfile）。
# 复用 M01 node 基座（本身 FROM node:22-alpine）；避免沙箱构建重复拉取基础镜像。
FROM widgetpilot/node-base:dev

ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry ${NPM_REGISTRY}

ENV TMPDIR=/tmp \
    NODE_ENV=production \
    HOME=/tmp

WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund --omit=dev --registry=${NPM_REGISTRY}
COPY runner.js ./

# 非 root UID 与宿主机 --user 10001:10001 一致；根文件系统只读，运行时只写 /tmp。
RUN chown -R 10001:10001 /app

USER 10001:10001
ENTRYPOINT ["node", "/app/runner.js"]
