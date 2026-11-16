#!/usr/bin/env python3
"""M02 §4.1.2 gen_python.py —— 生成 packages/sdk/python/（Pydantic v2 模型 + 客户端骨架）。
输入只有 packages/contracts（单一事实来源）；幂等（同输入同输出，§4.5）。
生成物：models.py（4 份 schema → Pydantic）+ client.py（REST 骨架）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # packages/contracts
SDK_PY = HERE.parent / "sdk" / "python" / "widgetpilot_client"

SCHEMA_FILES = [
    ("AgentEvent", "events.schema.json"),
    ("WidgetSpec", "widget_spec.schema.json"),
    ("WidgetBundle", "widget_bundle.schema.json"),
    ("WidgetRuntime", "widget_runtime.schema.json"),
]

PY_TYPE = {
    "string": "str", "integer": "int", "number": "float",
    "boolean": "bool", "object": "dict[str, Any]", "array": "list[Any]",
}


def py_field(name: str, node: dict, required: bool) -> str:
    t = node.get("type")
    if node.get("enum") or "const" in node:
        t = "str"
    py_t = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}.get(t, "Any")
    if t == "array":
        py_t = "list[Any]"
    if t == "object":
        py_t = "dict[str, Any]"
    if "$ref" in node:
        py_t = str(node["$ref"]).split("/")[-1]
    ann = f"{name}: {py_t}" if required else f"{name}: {py_t} | None = None"
    return f"    {ann}"


def model_code(name: str, schema: dict) -> str:
    lines = [f"class {name}(BaseModel):", '    """自动生成（禁止手改）；源: schema"""']
    props = schema.get("properties", {})
    required = schema.get("required", [])
    if not props:
        lines.append("    model_config = ConfigDict(extra='allow')")
    for k, v in props.items():
        if "$ref" in v and not v.get("type"):
            ref = str(v["$ref"]).split("/")[-1]
            req = k in required
            lines.append(f"    {k}: {ref}" + ("" if req else " | None = None"))
        else:
            lines.append(py_field(k, v, k in required))
    if len(lines) == 2:
        lines.append("    pass")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    SDK_PY.mkdir(parents=True, exist_ok=True)
    out = [
        '"""packages/sdk/python —— 自动生成物（M02 §7.2），禁止手改。"""',
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "from pydantic import BaseModel, ConfigDict",
        "",
    ]
    models: list[str] = []
    for name, f in SCHEMA_FILES:
        schema = json.loads((HERE / f).read_text(encoding="utf-8"))
        models.append(name)
        out.append(model_code(name, schema))

    client = [
        '"""REST 客户端骨架（M02 §7.2 生成物）；错误响应统一按 ApiResponse 解析。"""',
        "from __future__ import annotations",
        "",
        "import httpx",
        "",
        "from .models import ApiResponse, AgentEvent",
        "",
        "",
        "class WidgetPilotClient:",
        '    """control-plane REST 客户端；权限由服务端按 user_id 裁决（BFF 不做判定）。"""',
        "",
        "    def __init__(self, base_url: str = 'http://127.0.0.1:8000', token: str | None = None):",
        "        self._base = base_url.rstrip('/')",
        "        self._headers = {'Authorization': f'Bearer {token}'} if token else {}",
        "",
        "    def request(self, method: str, path: str, **kw) -> ApiResponse:",
        "        r = httpx.request(method, self._base + path, headers=self._headers, **kw)",
        "        return ApiResponse.model_validate(r.json())",
        "",
        "    def parse_event(self, raw: dict) -> AgentEvent:",
        '        """严格校验（不信任模型输出）。"""',
        "        return AgentEvent.model_validate(raw)",
    ]
    (SDK_PY / "__init__.py").write_text(
        "from .models import AgentEvent, WidgetSpec, WidgetBundle, WidgetRuntime, ApiResponse  # noqa: F401\n"
    )
    (SDK_PY / "models.py").write_text("\n".join(out))
    (SDK_PY / "client.py").write_text("\n".join(client))
    print(f"gen:python OK → {SDK_PY}（models: {', '.join(models)}）")


if __name__ == "__main__":
    sys.exit(main())
