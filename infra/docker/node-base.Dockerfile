# M01 §3.3 L1 基础层：Node 基座（web / bff 共享）
# 构建后由 web/bff 薄 Dockerfile FROM 引用：widgetpilot/node-base:dev
FROM node:22-alpine

# 国内构建：npm 走 npmmirror（--build-arg 可覆盖）
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN corepack enable && npm config set registry ${NPM_REGISTRY}
