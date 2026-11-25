"""P2 会话 widget 注册表 · target 定位器（ADR-013 §3.5）。

基于会话内 widget bundle 列表 + spec 槽位文本做**确定性定位**（零模型）：
- last：最新（输入列表按 created_at 降序）
- ordinal：会话内序号（1-based）
- by_host / by_content：匹配 spec.intent / layout_slots 文本
- all：全部返回

定位不明（多命中/零命中/越界序号）一律返回 None 或 choices，交上层反问，
不硬猜——反问带每个 widget 精准信息（bundle_id/grid/host/data/created_at）。
"""
from __future__ import annotations

from typing import cast

from packages.agent_core.types import JsonObject

__all__ = ["ambiguous_choices", "resolve_target"]

_HOST_TERMS = ("主屏幕", "主屏", "桌面", "锁屏", "负一屏")
_DATA_TERMS = ("待办", "清单", "任务", "天气", "日期", "日历", "时钟", "股票", "心率", "步数")


def _text(bundle: JsonObject) -> str:
    spec = bundle.get("spec")
    parts: list[str] = []
    if isinstance(spec, dict):
        for key in ("intent", "host", "data"):
            value = spec.get(key)
            if isinstance(value, str):
                parts.append(value)
        slots = spec.get("layout_slots")
        if isinstance(slots, list):
            for slot in slots:
                if isinstance(slot, dict):
                    for key in ("slot", "content"):
                        value = slot.get(key)
                        if isinstance(value, str):
                            parts.append(value)
    return " ".join(parts)


def _field(bundle: JsonObject, name: str) -> str:
    spec = bundle.get("spec")
    if isinstance(spec, dict):
        value = spec.get(name)
        if isinstance(value, str):
            return value
    text = _text(bundle)
    terms = _HOST_TERMS if name == "host" else _DATA_TERMS
    return next((t for t in terms if t in text), "")


def ambiguous_choices(bundles: list[JsonObject]) -> list[JsonObject]:
    """每个 widget 的精准信息（供反问让用户选，不说空泛的"无法确定"）。"""
    out: list[JsonObject] = []
    for b in bundles:
        spec = b.get("spec")
        grid = ""
        if isinstance(spec, dict):
            grid = str(spec.get("grid", ""))
        out.append({
            "bundle_id": str(b.get("bundle_id", "")),
            "grid": grid,
            "host": _field(b, "host"),
            "data": _field(b, "data"),
            "created_at": str(b.get("created_at", "")),
        })
    return out


def resolve_target(
    bundles: list[JsonObject],
    method: str,
    *,
    ordinal: int = 0,
    host: str = "",
    content: str = "",
) -> JsonObject | list[JsonObject] | None:
    """按 method 定位 widget；不明直接返回 {"choices": 精准信息列表} 供反问。"""
    if method == "all":
        return list(bundles)
    if not bundles:
        return None
    if method == "last":
        return max(bundles, key=lambda b: str(b.get("created_at", "")))
    if method == "ordinal":
        # 用户说"第二个"=按创建时间正序的第 2 个（直觉顺序）；store 传入的
        # 列表是 created_at 降序，必须先升序排回再取，否则语义反了。
        ordered = sorted(bundles, key=lambda b: str(b.get("created_at", "")))
        if ordinal < 1 or ordinal > len(ordered):
            return cast(JsonObject, {"choices": ambiguous_choices(bundles)})
        return ordered[ordinal - 1]
    if method == "by_host":
        hits = [b for b in bundles if host and host in _text(b)]
        # 零命中也要给全部候选（反问带精准信息），不能返回空 choices（信息丢失）。
        return hits[0] if len(hits) == 1 else cast(
            JsonObject, {"choices": ambiguous_choices(hits if hits else bundles)}
        )
    if method == "by_content":
        hits = [b for b in bundles if content and content in _text(b)]
        return hits[0] if len(hits) == 1 else cast(
            JsonObject, {"choices": ambiguous_choices(hits if hits else bundles)}
        )
    return cast(JsonObject, {"choices": ambiguous_choices(bundles)})
