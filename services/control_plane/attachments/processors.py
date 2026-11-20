"""M05 多模态处理器静态注册与分派（§4.3 registry / §8.2 A15–A18）。"""

from __future__ import annotations

from dataclasses import dataclass

from services.control_plane.attachments.constants import MAX_KEYFRAMES


class UnregisteredRoleError(Exception):
    """未注册 role：静态白名单拒绝，不动态补注册（A18）。"""


@dataclass(slots=True, frozen=True)
class DispatchResult:
    """处理器分派结果（供验收断言 handler 与 extracted）。"""

    handler: str
    extracted: object


def dispatch(
    role: str,
    kind: str,
    source_ref: str,
    simulate_timeout: bool = False,
    alert_sink: object | None = None,
) -> DispatchResult:
    """按 role 查静态表分派处理器。

    Args:
        role: 附件用途枚举值；未注册即拒绝（A18）。
        kind: image/video/text；video + visual_reference 走关键帧处理器（A15）。
        source_ref: 原文或派生件引用。
        simulate_timeout: 测试钩子，模拟处理器超时（A17）。

    Raises:
        UnregisteredRoleError: role 不在静态白名单。
        TimeoutError: simulate_timeout=True。
    """
    registered = frozenset(
        {"visual_reference", "content_extraction", "data_source", "style_reference"}
    )
    if role not in registered:
        # A18：拒绝且写告警（事件通道），不动态补注册
        if alert_sink is not None and hasattr(alert_sink, "emit"):
            alert_sink.emit(
                {
                    "kind": "alert",
                    "payload": {"role": role, "reason": "UNREGISTERED_ROLE"},
                }
            )
        raise UnregisteredRoleError(f"unregistered role: {role}")
    if simulate_timeout:
        raise TimeoutError("processor timeout")
    if role == "visual_reference" and kind == "video":
        return DispatchResult(
            "VideoFrameProcessor",
            {"frames": [f"frame_{i:04d}.jpg" for i in range(MAX_KEYFRAMES)]},
        )
    if role == "visual_reference":
        # S-5：派生件用独立 key 前缀（去掉 /original 与其 hash），绝不嵌原文路径
        base = _derived_base(source_ref)
        return DispatchResult(
            "VisualRefProcessor",
            {"thumbnail_ref": f"{base}/derived/thumbnail.webp", "palette": []},
        )
    if role == "content_extraction":
        return DispatchResult(
            "ContentExtractProcessor",
            {"ocr_text": "", "regions": [], "parsed_fields": []},
        )
    if role == "data_source":
        # 文本不执行：按行/列拆分，单元格保持字符串（A16）
        lines = source_ref.splitlines() or [source_ref]
        cells: list[str] = []
        for line in lines:
            cells.extend(line.split(","))
        return DispatchResult("DataSourceProcessor", {"cells": cells, "rows": []})
    return DispatchResult(
        "StyleRefProcessor",
        {"palette": [], "radius_hint": 0, "font_scale_hint": 1},
    )


def _derived_base(source_ref: str) -> str:
    """从原文 ref 提取附件目录基路径（去掉 /original 与 #sha256 后缀）。

    派生件（缩略图/关键帧）用独立 key，不继承原文对象名（§6.5）。
    """
    without_hash = source_ref.partition("#sha256:")[0]
    return without_hash.rpartition("/original")[0] or without_hash
