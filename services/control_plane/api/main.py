"""control-plane API 入口（M01 §4.1 full 组 :8000）。

M04 落地后：挂载 M04 REST 路由（§4.1），生产用真实 PG 存储（users/sessions），
保留 /healthz /readyz 探针（M01）。DATABASE_URL 与 JWT_SECRET 从环境注入
（12-factor，M01 §4.3）；缺任一则拒绝启动（安全/数据门禁）。
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from services._health import create_health_app
from services.control_plane.accounts.constants import SSE_WINDOW_N
from services.control_plane.accounts.pg_stores import PgUserStore
from services.control_plane.api.server import create_app as create_m04_app
from services.control_plane.attachments.events import RedisEventSink
from services.control_plane.attachments.pg_stores import PgAttachmentStore
from services.control_plane.attachments.processor_adapter import RealProcessor
from services.control_plane.attachments.s3_store import S3ObjectStore
from services.control_plane.attachments.service import AttachmentService
from services.control_plane.attachments.tenant_guard import SessionTenantGuard
from services.control_plane.audit.pg_ledger import PgAuditLedger
from services.control_plane.sessions.pg_stores import PgSessionStore
from services.control_plane.sessions.sse import RedisEventSource
from services.orchestrator.adapters import RedisEventBus, make_event_decoder
from services.orchestrator.dispatcher import TASK_QUEUE_KEY, RedisTaskQueue
from services.orchestrator.memory.pg_stores import PgPreferenceStore

# 环境变量名（结构性字面量集中一处）
_ENV_JWT_SECRET = "JWT_SECRET"
_ENV_DATABASE_URL = "DATABASE_URL"
_ENV_REDIS_URL = "REDIS_URL"
# asyncpg URL → psycopg 同步 URL（存储层用同步连接）
_ASYNCPG_PREFIX = "postgresql+asyncpg://"
_SYNC_PREFIX = "postgresql://"


def _sync_dsn(url: str) -> str:
    """把 asyncpg DSN 转为 psycopg 同步 DSN（存储层用同步连接）。"""
    if url.startswith(_ASYNCPG_PREFIX):
        return url.replace(_ASYNCPG_PREFIX, _SYNC_PREFIX, 1)
    return url


def create_prod_app() -> FastAPI:
    """构造生产 app：健康探针 + M04 路由 + 真实 PG 存储。

    Raises:
        RuntimeError: JWT_SECRET 或 DATABASE_URL 未配置（安全/数据门禁，
            无密钥或不明确数据库不启动）。
    """
    secret = os.getenv(_ENV_JWT_SECRET)
    database_url = os.getenv(_ENV_DATABASE_URL)
    if not secret:
        raise RuntimeError(f"{_ENV_JWT_SECRET} must be set")
    if not database_url:
        raise RuntimeError(f"{_ENV_DATABASE_URL} must be set")

    dsn = _sync_dsn(database_url)
    redis_url = os.getenv(_ENV_REDIS_URL, "redis://redis:6379/0")
    # SSE 生产事件源：Redis Streams → SSEEvent（§4.1 events 端点真实扇出）
    bus = RedisEventBus(redis_url, SSE_WINDOW_N, make_event_decoder())
    event_source = RedisEventSource(bus.window, SSE_WINDOW_N)
    user_store = PgUserStore(dsn)
    session_store = PgSessionStore(dsn)
    # M05 生产附件服务：真实 S3 对象存储 + PG 索引 + 真实处理器 + 会话归属守卫
    attachments = AttachmentService(
        objects=S3ObjectStore(),
        attachments=PgAttachmentStore(dsn),
        tenant=SessionTenantGuard(session_store),
        processor=RealProcessor(),
        event_sink=RedisEventSink(bus, redis_url),  # A8：事件跨 worker 可达 SSE
    )
    task_queue = RedisTaskQueue(redis_url, TASK_QUEUE_KEY)
    app = create_m04_app(
        user_store,
        session_store,
        secret,
        event_source=event_source,
        attachment_service=attachments,
        audit=PgAuditLedger(dsn),  # A18：审计落 PG，跨实例可重放（B2 生产接线）
        task_enqueuer=task_queue.enqueue,  # B1b：提交描述真实入队，驱动编排
        preference_store=PgPreferenceStore(dsn),  # M07 B6：偏好 REST 生产真实 PG
    )
    health = create_health_app("control-plane", 8000)

    # 挂载健康探针路由（M01 §5.4：/healthz 200=健康；/readyz 依赖就绪）
    for route in health.routes:
        if getattr(route, "path", "") in ("/healthz", "/readyz"):
            app.router.routes.append(route)
    return app


app = create_prod_app()
