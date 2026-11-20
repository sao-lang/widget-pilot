"""真实处理器统一入口：role + kind → ProcessorResult（服务层注入用）。"""

from __future__ import annotations

from dataclasses import dataclass

from services.control_plane.attachments.real_processors import (
    ProcessorResult,
    process_data_source,
    process_ocr,
    process_video,
    process_visual,
)


@dataclass(slots=True, frozen=True)
class RealProcessor:
    """按 role/kind 分派真实处理器（图片主色/OCR/关键帧/文本解析）。"""

    def process(
        self, data: bytes, role: str, kind: str, source_ref: str
    ) -> ProcessorResult:
        """按 role 分派；video 走关键帧，其余按 role。

        Args:
            data: 附件原文字节。
            role: AttachmentRole 值。
            kind: image/video/text。
            source_ref: 原文引用（溯源用，不嵌入 extracted）。

        Returns:
            ProcessorResult（含真实 extracted 与 insight_quality）。
        """
        if kind == "video":
            return process_video(data)
        if role == "visual_reference":
            return process_visual(data, source_ref)
        if role == "content_extraction":
            return process_ocr(data)
        if role == "data_source":
            return process_data_source(data)
        return ProcessorResult(
            "StyleRefProcessor",
            {"style_words": [], "palette": [], "radius_hint": 0},
            1.0,
        )
