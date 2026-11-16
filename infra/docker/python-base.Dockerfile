# M01 §3.3 L1 基础层：Python 依赖基座（全服务共享，层缓存最大化）
# 构建后由各服务薄 Dockerfile FROM 引用：widgetpilot/python-base:dev
# 国内构建：PyPI 走阿里云镜像（--build-arg 可覆盖）
FROM python:3.12-slim-bookworm

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_INDEX_URL=${PIP_INDEX_URL} UV_INDEX_URL=${PIP_INDEX_URL} UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

# 系统包：ffmpeg（M05 视频关键帧处理器）；Debian 源走阿里云镜像（--build-arg 可覆盖）
ARG DEBIAN_MIRROR=mirrors.aliyun.com
RUN sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 仅依赖层：先拷锁文件（变更频率最低，最大化缓存命中）
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 统一非 root 运行用户（M01 §3.3：uid/gid 10001）
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --shell /usr/sbin/nologin app
USER 10001
WORKDIR /app
