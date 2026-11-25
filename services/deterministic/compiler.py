"""确定性编译器：成分 SlotFill → WidgetSpec(16) → 白名单组件 TSX（ADR-013 §3.4）。

- 不建模板库（用户否决）：TSX 走 WidgetSpec → 白名单组件封闭渲染。
- 零模型、零副作用：输入成分，输出合法 spec 与源码字符串。
- TSX 契约自洽：widget_type=custom；intent 特征词体现在 layout_slots；
  grid 不冲突、density 不超；只 import 白名单、default Widget；bridge 能力
  调用一律在 capabilities/manifest 声明，避免输出护栏与契约校验拒绝。
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

from packages.agent_core.types import JsonObject, JsonValue
from services.guardrails.interpreter.types import SlotFill

__all__ = [
    "assert_tsx_contract_ok",
    "assert_valid_spec",
    "build_manifest",
    "compile_spec",
    "render_tsx",
]

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "widget_spec.schema.json"
_SPEC_SCHEMA = Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))
_NAMESPACE = uuid.UUID("6f8f6f00-0000-4000-8000-000000000001")

# 意图特征 → 封闭组件族（与 output_guard._FEATURE_SLOT_TERMS 同源，不引模型）。
# key 是描述里 data token 的特征词，value 是 (intent 文本片段, layout_slot content)。
_FEATURE_RENDER: tuple[tuple[tuple[str, ...], str, str, tuple[str, ...], str], ...] = (
    (("待办", "清单", "任务", "勾选"), "任务清单", "任务列表", ("static_data",), "recipe.task.list.v2"),
    (("日期", "日历", "月", "周"), "日期日历", "日期网格", ("date",), "recipe.calendar.month.v3"),
    (("时间", "时钟", "倒计时", "计时"), "时钟", "时间显示", ("timer",), "recipe.clock.display.v1"),
    (("天气", "气温", "预报"), "天气", "天气信息", ("static_data",), "recipe.weather.card.v1"),
    (("步数", "心率", "健康", "运动"), "健康数据", "健康指标", ("static_data",), "recipe.health.metrics.v1"),
    (("股票", "基金", "行情"), "行情", "行情列表", ("static_data",), "recipe.market.quotes.v1"),
)

_DEFAULT_RECIPE = "recipe.task.list.v2"


def _feature(data: str) -> tuple[str, str, tuple[str, ...], str]:
    for keys, intent_text, slot_text, caps, recipe_id in _FEATURE_RENDER:
        if any(k in data for k in keys):
            return intent_text, slot_text, caps, recipe_id
    # 未知特征 → custom 通用渲染，不猜具体能力
    return "自定义控件", "内容区", ("static_data",), _DEFAULT_RECIPE


def recipe_for_feature(data: str) -> str:
    """按 data 特征映射 recipe_id（零模型，封闭表）。"""
    return _feature(data)[3]


# v2fix：颜色 token → hex（封闭映射，零模型）。只有明确色名/hex 才归一化，
# 描述性短语（"科技感渐变"等）返回空串，不写进 spec 造成假绿。
_COLOR_NAMES: dict[str, str] = {
    "黑": "#000000", "白": "#FFFFFF", "灰": "#808080", "红": "#E24B4A",
    "蓝": "#378ADD", "绿": "#639922", "黄": "#EF9F27", "紫": "#7F77DD",
    "橙": "#D85A30", "粉": "#D4537E", "青": "#1D9E75",
}
_COLOR_QUALIFIERS = ("科技", "极简", "磨砂", "深色", "浅色", "暗", "亮", "素", "高级", "风", "色", "配色", "主题")


def _color_to_hex(value: str) -> str:
    """颜色 token → hex；非颜色返回空串（不臆造）。"""
    token = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", token):
        return token.upper()
    # 逐个色名匹配；「科技蓝」→ #378ADD，忽略限定词
    for name, hex_value in _COLOR_NAMES.items():
        if name in token:
            return hex_value
    return ""


def compile_spec(fill: SlotFill, *, recipe_id: str | None = None) -> JsonObject:
    """成分 → 16 字段 WidgetSpec；widget_id = uuid5(namespace, spec_hash)。"""
    slots = fill.slots
    grid = str(slots.get("grid", "4x2"))
    data = str(slots.get("data", ""))
    host = str(slots.get("host", ""))
    intent_text, slot_text, caps, derived_recipe = _feature(data)
    recipe_id = recipe_id or derived_recipe
    intent = f"{host}{intent_text}" if host else intent_text

    # 附件派生产物（frame_at/crop/csv.filter 的 derived ref）进 layout_slots，
    # 让"视频第 3.5 秒帧做底图"等用途真实体现在产物槽位（ADR-013 §3.3）。
    derived = slots.get("derived_refs")
    content = slot_text
    if isinstance(derived, dict) and derived:
        refs = "; ".join(f"{k}={v}" for k, v in derived.items() if isinstance(v, str))
        if refs:
            content = f"{slot_text} [{refs}]"

    # v2fix：显式 style 槽位进 spec.style.palette（schema 已有字段，
    # bundles/service.py 采纳时真消费）。只接受可归一化颜色 token，
    # 避免把"想还是别用彩色了改成…"这类误抽写进产物。
    style_value = slots.get("style", "")
    style_spec: JsonObject = {}
    if isinstance(style_value, str):
        palette = _color_to_hex(style_value)
        if palette:
            style_spec["palette"] = [palette]
    elif isinstance(style_value, (list, tuple)):
        colors = [_color_to_hex(str(c)) for c in style_value]
        resolved: list[JsonValue] = [c for c in colors if c]
        if resolved:
            style_spec["palette"] = resolved

    spec: JsonObject = {
        "spec_version": "1.0",
        "widget_id": "",  # 先占位，算完 hash 再填（uuid5 可复现，不随机）
        "grid": grid,
        "widget_type": "custom",
        "intent": intent,
        "attachments": [],
        "layout_slots": [{"slot": "main", "content": content}],
        "capabilities": list(caps),
        "data_contract": {"sources": ["static"], "storage_namespace": f"deterministic/{recipe_id}"},
        "style": style_spec,
        "constraints": {"forbidden": [], "max_elements": 16},
        "language": "tsx",
        "react_version": "19",
        "import_allowlist": ["react", "@widget/bridge"],
        "export_contract": {"default": "Widget"},
        "props_contract": ["grid", "theme", "locale", "tick", "data"],
    }
    # spec_hash 用最终字段算（此时 widget_id 仍占位），再 uuid5 回填；
    # 回填后 spec_hash 会变，但 widget_id 由「除 id 外的成分」决定 → 稳定可复现。
    seed = json.dumps({k: v for k, v in spec.items() if k != "widget_id"},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    spec["widget_id"] = "w-" + uuid.uuid5(_NAMESPACE, seed).hex
    return spec


def assert_valid_spec(spec: JsonObject) -> None:
    """真 JSON Schema 校验；非法直接抛（fail-closed）。"""
    errors = sorted(_SPEC_SCHEMA.iter_errors(spec), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        raise ValueError(f"WidgetSpec schema invalid: {'.'.join(map(str, first.absolute_path))}: {first.message}")


_MAX_INLINE_IMAGE_BYTES = 64 * 1024
_IMAGE_SUFFIX_MIME = {".webp": "webp", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}


def embed_assets(
    spec: JsonObject, derived_refs: object, reader: object
) -> None:
    """把派生图片 ref 读成 base64 data URL 注入 spec（就地修改，无返回）。

    P0 附件生成侧：沙箱 --network none，S3 key/ref 不可达，唯一可出像素的形态
    是内联 data URL。只处理图片后缀、<=64KB、magic bytes 匹配的派生，超大或
    非图片一律跳过（保持纯文本，不炸产物/不假绿）。读失败也不阻断生成。
    """
    slots_value = spec.get("layout_slots")
    if not isinstance(derived_refs, dict) or not isinstance(slots_value, list) or not slots_value:
        return
    get = getattr(reader, "get", None)
    if not callable(get):
        return
    for ref in derived_refs.values():
        if not isinstance(ref, str) or "#sha256:" not in ref:
            continue
        key = ref.partition("#sha256:")[0].lower()
        if not any(key.endswith(suf) for suf in _IMAGE_SUFFIX_MIME):
            continue  # 非图片派生（csv/json 文本）不注入
        try:
            raw = get(ref)
        except Exception:  # noqa: BLE001, S112 - 产物资产读失败降级纯文本，不阻断
            continue
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            continue
        data = bytes(raw)
        if not data or len(data) > _MAX_INLINE_IMAGE_BYTES:
            continue
        # MIME 以 magic bytes 为准（后缀可能不准），三者都不匹配则跳过。
        if data.startswith(b"\x89PNG"):
            mime = "png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "webp"
        elif data.startswith(b"\xff\xd8\xff"):
            mime = "jpeg"
        else:
            continue
        slot = slots_value[0]
        if isinstance(slot, dict):
            slot["image_data_url"] = (
                f"data:image/{mime};base64," + base64.b64encode(data).decode()
            )
        return  # 只嵌第一张图（单 slot 结构，多图排 P6）


def _safe_image_data_url(raw: object) -> str:
    """白名单校验内联图片：非法内容一律降级为空串（不注入 JSX）。

    沙箱 --network none，data URL 是唯一可出像素的形态；但来源是不可信的
    派生 ref 字节，必须严格限制 MIME + base64 字符集，杜绝 JSX 注入。
    """
    if not isinstance(raw, str):
        return ""
    return raw if re.fullmatch(
        r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+", raw
    ) else ""


def render_tsx(spec: JsonObject) -> str:
    """WidgetSpec → 封闭组件 TSX：只渲染白名单结构，文本一律 {} 插值。"""
    assert_valid_spec(spec)
    layout = cast(list[JsonValue], spec["layout_slots"])
    first_slot = cast(dict, layout[0]) if layout else {}
    content = str(first_slot.get("content", "内容"))
    image_url = _safe_image_data_url(first_slot.get("image_data_url"))
    caps = cast(list[str], spec["capabilities"])
    # 只有真正需要宿主能力的 widget 才 import/call bridge（契约声明 ↔ 代码一致）。
    # static_data 是纯 props 数据渲染，不调 bridge——既诚实，也让无 bridge stub 的
    # 沙箱镜像能真编译渲染（能力调用与声明一致由 check_contract 复核）。
    bridge_caps = {"date", "timer", "local_storage", "theme", "locale"}
    needs_bridge = any(c in bridge_caps for c in caps)
    bridge_import = 'import bridge from "@widget/bridge";\n' if needs_bridge else ""
    call_line = ""
    if needs_bridge:
        first = next(c for c in caps if c in bridge_caps)
        call_line = f'  bridge.call("{first}");\n'
    # 图片作为 JS 字符串字面量（json.dumps 转义），白名单校验已保证无引号/
    # 花括号，json.dumps 再兜底一层；禁 JSX 属性内直接插值防注入。
    image_node = (
        f'      <img alt="" src={{{json.dumps(image_url)}}} />\n'
        if image_url else ""
    )
    return (
        'import React from "react";\n'
        f'{bridge_import}'
        'export default function Widget(props) {\n'
        f'{call_line}'
        '  const items = Array.isArray(props.data) ? props.data : [];\n'
        '  return (\n'
        '    <div style={{width: "100%", height: "100%"}}>\n'
        f'{image_node}'
        f'      <span>{content}</span>\n'
        '      {items.map((it, i) => <div key={i}>{String(it)}</div>)}\n'
        '    </div>\n'
        '  );\n'
        '}\n'
    )


def build_manifest(spec: JsonObject, *, recipe_id: str | None = None) -> JsonObject:
    """spec → manifest_draft；spec_hash/grid/capabilities 与 spec 严格一致。"""
    from services.guardrails.contract_check import compute_spec_hash

    if recipe_id is None:
        dc = spec.get("data_contract")
        ns = str(dc.get("storage_namespace", "")) if isinstance(dc, dict) else ""
        recipe_id = ns.rsplit("/", 1)[-1] or _DEFAULT_RECIPE

    return {
        "spec_version": "v1",
        "spec_hash": compute_spec_hash(spec),
        "grid": spec["grid"],
        "widget_type": "custom",
        "capabilities": list(cast(list[str], spec["capabilities"])),
        "import_allowlist": ["react", "@widget/bridge"],
        "exports": {"default": "Widget"},
        "props": list(cast(list[str], spec["props_contract"])),
        "recipe_version": recipe_id,
        "retrieval_snapshot": "deterministic",
        "model": "deterministic",
        "prompt_version": "v3",
        "model_tier": "cheap",
    }


def assert_tsx_contract_ok(spec: JsonObject, tsx: str) -> None:
    """用真静态扫描 + 契约校验断言 TSX 自洽（不跑沙箱、不调模型、不依赖测试桩）。"""
    from services.guardrails.contract_check import check_contract
    from services.guardrails.static_scan import parse_tsx

    ast = parse_tsx(tsx)
    report = check_contract(spec, build_manifest(spec), ast)
    if not report.passed:
        raise ValueError(f"TSX contract failed: {report.violations}")
