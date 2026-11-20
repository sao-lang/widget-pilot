"""guardrails API 入口（:8100；护栏判据实现归 M06/M08）。

M06 落地 REST 端点（§4.1）：
- GET /api/guardrails/hits   —— guard_hit 命中列表 + FPR（【推导·需评审】）
- GET /api/skills            —— 技能 manifest 只读查询（【推导·需评审】/可选）

健康探针 /healthz /readyz 沿用 M01。存储只有生产 PgGuardHitStore（读 DATABASE_URL）；
测试内存替身位于 tests/support/guardrails.py，源码不保留默认桩。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query

from packages.contracts.enums import GridSpec, WidgetType
from packages.contracts.types import ApiEnvelope, JsonValue
from services._health import create_health_app
from services.guardrails.guard_hit import GuardHitRow, GuardHitStore
from services.guardrails.guard_hit_pg import PgGuardHitStore
from services.guardrails.rule_registry import _mask
from services.guardrails.skill_router import _PREFETCH_MAP
from services.guardrails.types import GuardHitType


def _ok(data: JsonValue) -> ApiEnvelope:
    """统一 ApiResponse 信封（M4，v1.8）：契约 required 五字段齐全。

    Args:
        data: 成功载荷（放 data 字段）。

    Returns:
        符合 openapi.yaml ApiResponse 的响应字典。
    """
    return {
        "code": "OK",
        "message": "OK",
        "traceId": uuid4().hex,
        "retryable": False,
        "data": data,
    }


def create_app(
    hit_store: GuardHitStore,
    labeled_should_pass: int = 0,
) -> FastAPI:
    """构造 guardrails app：健康探针 + M06 只读查询端点。

    Args:
        hit_store: guard_hit 存储（生产为 PgGuardHitStore；测试注入 tests/support 替身）。
        labeled_should_pass: FPR 分母（标注「应放行」总数；默认 0 → FPR=0）。

    Returns:
        带 M06 端点的 FastAPI app。
    """
    app = create_health_app("guardrails", 8100)
    store = hit_store

    @app.get("/api/guardrails/hits")
    async def list_hits(
        hit_type: str | None = Query(default=None),
        rule_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        cursor: int = Query(default=0, ge=0),
    ) -> dict:
        """guard_hit 命中查询 + 误拦截率（§4.1 / §3.4 E）。"""
        # 非法枚举返回 422，不静默空列表（review N3）
        if hit_type is not None and hit_type not in {h.value for h in GuardHitType}:
            raise HTTPException(status_code=422, detail="illegal hit_type")
        rows: list[GuardHitRow] = list(store.rows)
        if hit_type is not None:
            rows = [r for r in rows if r.hit_type.value == hit_type]
        if rule_id is not None:
            rows = [r for r in rows if r.rule_id == rule_id]
        page = rows[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(rows) else None
        return _ok(
            {
                "items": [
                    {
                        # m1（v1.8）：契约 GuardHit.id 为 string，运行时 int → 转 str
                        "id": str(r.id),
                        "hit_type": r.hit_type.value,
                        "rule_id": r.rule_id,
                        "judgement": r.judgement,
                        # 对外回传前再掩码一次（§6.5 红线：接口不回未脱敏原文；
                        # 注入片段虽非 PII，亦按同口径截断/掩码，端点评审前收敛暴露面）
                        "excerpt": _mask(r.excerpt, r.hit_type, r.rule_id),
                        "trace_id": r.trace_id,
                    }
                    for r in page
                ],
                "false_positive_rate": store.false_positive_rate(labeled_should_pass),
                "next_cursor": next_cursor,
            }
        )

    @app.get("/api/skills")
    async def list_skills(
        widget_type: str | None = Query(default=None),
        grid: str | None = Query(default=None),
    ) -> dict:
        """技能 manifest 只读查询（§4.1）；只读静态预取映射，不触发生成。"""
        if widget_type is not None and widget_type not in {w.value for w in WidgetType}:
            raise HTTPException(status_code=422, detail="illegal widget_type")
        if grid is not None and grid not in {g.value for g in GridSpec}:
            raise HTTPException(status_code=422, detail="illegal grid")
        items = []
        for (wt, gs), recipe_ids in _PREFETCH_MAP.items():
            if widget_type is not None and wt.value != widget_type:
                continue
            if grid is not None and gs.value != grid:
                continue
            items.append(
                {
                    "widget_type": wt.value,
                    "grid": gs.value,
                    "recipe_ids": list(recipe_ids),
                }
            )
        return _ok({"items": items})

    return app


def _production_store() -> PgGuardHitStore:
    """构造生产 guard_hit 存储；缺少 DATABASE_URL 时 fail-fast，不降级内存。

    Returns:
        生产 PG store。

    Raises:
        RuntimeError: 未配置 DATABASE_URL（生产禁止无证据放行）。
    """
    import os

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL must be set for guardrails production store")
    return PgGuardHitStore(dsn)


type _Scope = dict[str, object]
"""ASGI scope（边界形态由服务器提供，无法静态收窄）。"""

type _Receive = Callable[[], Awaitable[dict[str, object]]]
"""ASGI receive 回调。"""

type _Send = Callable[[dict[str, object]], Awaitable[None]]
"""ASGI send 回调。"""


class LazyGuardrailsApp:
    """ASGI 入口的惰性包装：首次请求才构造生产 PG store。

    避免「import 模块即要求 DATABASE_URL」的副作用，工具/测试可以安全导入；
    生产首次请求时缺 DSN 仍由 `_production_store()` fail-fast。

    Attributes:
        _inner: 已构造的 FastAPI app；None 表示尚未收到请求。
    """

    def __init__(self) -> None:
        self._inner: FastAPI | None = None

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        """按 ASGI 协议懒构造 app 并转发请求。"""
        if self._inner is None:
            self._inner = create_app(hit_store=_production_store())
        await self._inner(scope, receive, send)  # type: ignore[arg-type]


app = LazyGuardrailsApp()
