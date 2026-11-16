"""FastAPI 应用工厂（M01 §8.1「最小健康壳」）。

当前阶段：各服务临时实现 /healthz，验证容器/端口/依赖注入（D2/D7）；
业务接口由 M04–M11 按 packages/contracts 契约逐步实现。

/healthz 语义（M01 §5.4）：200 = 健康；503 = 依赖不可达（按 M04/M11 语义上报）。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Response


def create_health_app(service_name: str, port: int) -> FastAPI:
    app = FastAPI(title=f"WidgetPilot {service_name}", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict:
        # 门禁常量在代码里（M01 §2.1）：不提供任何「关闭护栏/校验」的环境开关
        return {
            "status": "OK",
            "service": service_name,
            "env": os.getenv("ENV", "dev"),
            "port": port,
        }

    @app.get("/readyz")
    async def readyz() -> dict:
        # 依赖就绪探针：业务依赖（pg/redis/s3）连通性由各模块实现后接入
        return {"status": "OK", "service": service_name}

    return app
