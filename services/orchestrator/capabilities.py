"""能力首批注册（ADR-003 / Q2/Q4）——composition root 调用。

注册三类附件处理（视觉增强/OCR/视频）+ 代码执行 + 两个规程，随后冻结 REGISTRY。
附件 handler 只接收对象存储引用（source_ref），原文读取由注入的 blob_reader 完成，
不把原文塞进参数或 L0 目录（守住 M05 原文红线）。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from packages.agent_core.injection import ModelGateway
from packages.agent_core.tools.registry import Capability, freeze_registry, register
from services.control_plane.attachments.real_processors import (
    process_ocr,
    process_video,
    process_visual,
)
from services.control_plane.attachments.vision import understand_image
from services.orchestrator.recipe_loader import load_recipe

if TYPE_CHECKING:
    from packages.contracts.types import JsonObject


class AttachmentArgs(BaseModel):
    """附件处理参数：只含对象存储引用与可选提示。"""

    source_ref: str
    """原文在对象存储中的引用（不传原文）。"""
    prompt: str = "描述图像中的界面元素与布局"


class CodeArgs(BaseModel):
    """代码执行参数。"""

    code: str
    timeout_s: int = 10


class RecipeArgs(BaseModel):
    """规程加载参数（L2-skill 无需额外参数）。"""

    reason: str = ""


def _default_blob_reader(source_ref: str) -> bytes:
    """默认原文读取器：本地路径（开发/测试）；生产由 build_deps 注入 S3 reader。"""
    return Path(source_ref).read_bytes()


def _make_visual_handler(
    reader: Callable[[str], bytes], gateway: ModelGateway | None
) -> Callable[[AttachmentArgs], str]:
    """构造图片处理 handler：确定性主色/缩略图 + 视觉模型语义元素。"""

    def handler(args: AttachmentArgs) -> str:
        import json

        result = process_visual(reader(args.source_ref), args.source_ref)
        vision = understand_image(
            image_ref=str(result.extracted.get("thumbnail_ref", "")),
            prompt=args.prompt,
            gateway=gateway,
        )
        payload: JsonObject = {
            "handler": result.handler,
            "extracted": result.extracted,
            "vision_elements": list(vision.elements),
            "vision_degraded": vision.degraded,
            "insight_quality": result.insight_quality,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    return handler


def _make_ocr_handler(reader: Callable[[str], bytes]) -> Callable[[AttachmentArgs], str]:
    """构造 OCR handler：只传引用读取原文，返回结构化区域文本。"""

    def handler(args: AttachmentArgs) -> str:
        import json

        result = process_ocr(reader(args.source_ref))
        return json.dumps(
            {
                "handler": result.handler,
                "extracted": result.extracted,
                "insight_quality": result.insight_quality,
            },
            ensure_ascii=False,
            default=str,
        )

    return handler


def _make_video_handler(reader: Callable[[str], bytes]) -> Callable[[AttachmentArgs], str]:
    """构造视频 handler：关键帧 + 帧文本关联。"""

    def handler(args: AttachmentArgs) -> str:
        import json

        result = process_video(reader(args.source_ref))
        return json.dumps(
            {
                "handler": result.handler,
                "extracted": result.extracted,
                "insight_quality": result.insight_quality,
            },
            ensure_ascii=False,
            default=str,
        )

    return handler


def _make_code_handler() -> Callable[[CodeArgs], str]:
    """构造代码执行 handler：docker 一次性容器（Q5，隔离机制暂缺已登记）。"""

    def handler(args: CodeArgs) -> str:
        import json

        from services.sandbox_runner.executor import execute_code

        result = execute_code(args.code, args.timeout_s)
        return json.dumps(
            {
                "ok": result.ok,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "isolation_note": result.isolation_note,
            },
            ensure_ascii=False,
        )

    return handler


def register_capabilities(
    gateway: ModelGateway | None = None,
    blob_reader: Callable[[str], bytes] | None = None,
    *,
    include_code_exec: bool = True,
    include_attachments: bool = True,
) -> None:
    """注册首批能力并冻结注册表（Q2 composition root）。

    Args:
        gateway: 视觉理解用的 ModelGateway；None 表示当前环境无真实模型（降级）。
        blob_reader: 原文读取器；None 用本地路径默认实现。
        include_code_exec: 是否注册代码执行（隔离机制未落地时生产必须 False，S4）。
        include_attachments: 是否注册附件能力（无 S3 reader 时生产可关，S5）。
    """
    reader = blob_reader or _default_blob_reader
    if include_attachments:
        register(
            "tool.attachment.image",
            Capability(
                id="tool.attachment.image", kind="tool", brief="图片理解（主色/布局/视觉语义）",
                detail_ref="args.attachment", payload_ref="handler.attachment.image",
                surfaces=frozenset({"generation"}), cost_hint=120,
                args_model=AttachmentArgs, handler=_make_visual_handler(reader, gateway),
            ),
        )
        register(
            "tool.attachment.ocr",
            Capability(
                id="tool.attachment.ocr", kind="tool", brief="OCR 文字与区域提取",
                detail_ref="args.attachment", payload_ref="handler.attachment.ocr",
                surfaces=frozenset({"generation"}), cost_hint=80,
                args_model=AttachmentArgs, handler=_make_ocr_handler(reader),
            ),
        )
        register(
            "tool.attachment.video",
            Capability(
                id="tool.attachment.video", kind="tool", brief="视频关键帧与帧文本",
                detail_ref="args.attachment", payload_ref="handler.attachment.video",
                surfaces=frozenset({"generation"}), cost_hint=150,
                args_model=AttachmentArgs, handler=_make_video_handler(reader),
            ),
        )
    if include_code_exec:
        register(
            "tool.code.execute",
            Capability(
                id="tool.code.execute", kind="tool", brief="在一次性容器执行代码（无沙箱隔离）",
                detail_ref="args.code", payload_ref="handler.code", surfaces=frozenset({"generation"}),
                cost_hint=60, args_model=CodeArgs, handler=_make_code_handler(),
            ),
        )
    for recipe_id, cost in (("recipe.calendar.month.v3", 220), ("recipe.task.list.v2", 180)):
        register(
            recipe_id,
            Capability(
                id=recipe_id, kind="skill", brief=f"生成规程：{recipe_id}",
                detail_ref="manifest.recipe", payload_ref=f"body.{recipe_id}",
                surfaces=frozenset({"generation"}), cost_hint=cost,
                args_model=RecipeArgs, body=load_recipe(recipe_id).text,
            ),
        )
    freeze_registry()
