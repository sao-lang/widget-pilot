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
    "boolean": "bool", "object": "JsonObject", "array": "list[JsonValue]",
}

# $ref 形如 "./widget_spec.schema.json"——文件名不是合法 Python 标识符，
# 必须映射回本生成器产出的类名，否则生成物出现 `spec: widget_spec.schema.json`
# 这种非法注解（Pydantic 建模时直接报 undefined name）。
_REF_TO_CLASS = {filename: name for name, filename in SCHEMA_FILES}

# 统一响应信封：源为 openapi.yaml components.schemas.ApiResponse（M02 §4.4/§6.9）。
# 生成器只依赖标准库，故在此显式镜像字段清单；字段变更须同步 openapi.yaml。
_API_RESPONSE_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("code", "str", True),
    ("message", "str", True),
    ("traceId", "str", True),
    ("retryable", "bool", True),
    ("evidenceRef", "str", False),
    ("data", "JsonValue", False),
)


def _ref_name(ref: str) -> str:
    """把 $ref 尾段解析为生成物类名；未知引用回退为 JsonValue（fail-closed，不产非法注解）。"""
    tail = ref.split("/")[-1]
    return _REF_TO_CLASS.get(tail, "JsonValue")


def py_field(name: str, node: dict, required: bool) -> str:
    if node.get("enum") or "const" in node:
        py_t = "str"  # enum/const 是封闭字符串集合，不能用宽泛 JsonValue
    else:
        t = node.get("type") or ""
        py_t = PY_TYPE.get(t, "JsonValue")
    if "$ref" in node:
        py_t = _ref_name(str(node["$ref"]))
    ann = f"{name}: {py_t}" if required else f"{name}: {py_t} | None = None"
    return f"    {ann}"


def model_code(name: str, schema: dict) -> str:
    lines = [f"class {name}(BaseModel):", '    """自动生成（禁止手改）；源: schema"""']
    props = schema.get("properties", {})
    required = schema.get("required", [])
    if not props:
        lines.append("    model_config = ConfigDict(extra='allow')")
    for k, v in props.items():
        lines.append(py_field(k, v, k in required))
    if len(lines) == 2:
        lines.append("    pass")
    lines.extend(["", ""])
    return "\n".join(lines)


def api_response_code() -> str:
    """生成统一响应信封模型（字段镜像 openapi.yaml 的 ApiResponse）。"""
    lines = [
        "class ApiResponse(BaseModel):",
        '    """统一响应（源: openapi.yaml components.schemas.ApiResponse）"""',
    ]
    for field, py_t, required in _API_RESPONSE_FIELDS:
        ann = f"{field}: {py_t}" if required else f"{field}: {py_t} | None = None"
        lines.append(f"    {ann}")
    lines.extend(["", ""])
    return "\n".join(lines)


def main() -> None:
    SDK_PY.mkdir(parents=True, exist_ok=True)
    schemas = [
        (name, json.loads((HERE / f).read_text(encoding="utf-8")))
        for name, f in SCHEMA_FILES
    ]
    needs_config = any(not schema.get("properties") for _, schema in schemas)
    pydantic_import = (
        "from pydantic import BaseModel, ConfigDict"
        if needs_config
        else "from pydantic import BaseModel"
    )
    out = [
        '"""packages/sdk/python —— 自动生成物（M02 §7.2），禁止手改。"""',
        "from __future__ import annotations",
        "",
        pydantic_import,
        "",
        "# JSON 边界具名别名：生成物自包含（SDK 独立分发，不依赖 agent_core）。",
        "type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]",
        "type JsonObject = dict[str, JsonValue]",
        "",
        "",
    ]
    models: list[str] = []
    for name, schema in schemas:
        models.append(name)
        out.append(model_code(name, schema))
    models.append("ApiResponse")
    out.append(api_response_code())

    client = [
        '"""REST 客户端骨架（M02 §7.2 生成物）；错误响应统一按 ApiResponse 解析。"""',
        "from __future__ import annotations",
        "",
        "import httpx",
        "",
        "from .models import AgentEvent, ApiResponse",
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
        "from .client import WidgetPilotClient  # noqa: F401\n"
        "from .models import AgentEvent, ApiResponse, WidgetBundle, WidgetRuntime, WidgetSpec  # noqa: F401\n"
    )
    (SDK_PY / "models.py").write_text("\n".join(out))
    (SDK_PY / "client.py").write_text("\n".join(client))
    print(f"gen:python OK → {SDK_PY}（models: {', '.join(models)}）")


if __name__ == "__main__":
    sys.exit(main())
