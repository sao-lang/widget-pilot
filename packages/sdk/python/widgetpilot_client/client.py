"""REST 客户端骨架（M02 §7.2 生成物）；错误响应统一按 ApiResponse 解析。"""
from __future__ import annotations

import httpx

from .models import ApiResponse, AgentEvent


class WidgetPilotClient:
    """control-plane REST 客户端；权限由服务端按 user_id 裁决（BFF 不做判定）。"""

    def __init__(self, base_url: str = 'http://127.0.0.1:8000', token: str | None = None):
        self._base = base_url.rstrip('/')
        self._headers = {'Authorization': f'Bearer {token}'} if token else {}

    def request(self, method: str, path: str, **kw) -> ApiResponse:
        r = httpx.request(method, self._base + path, headers=self._headers, **kw)
        return ApiResponse.model_validate(r.json())

    def parse_event(self, raw: dict) -> AgentEvent:
        """严格校验（不信任模型输出）。"""
        return AgentEvent.model_validate(raw)