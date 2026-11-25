"""能力统一注册（ADR-003 首批 + ADR-014 多媒体工具）——composition root 调用。

一个文件完成全部注册：首批三类附件处理 + 代码执行 + 两个规程，以及 ADR-014
的 14 个图片/视频/文案工具，随后统一冻结 REGISTRY。不再拆出第二个注册模块，
避免双份 composition root 与 freeze 时序开关。

所有附件 handler 只接收对象存储引用（source_ref），原文读取由注入的 blob_reader
校验完成；派生字节写 derived/ 前缀并只回带 hash 的 ref，原文不进参数/L0/日志。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, Field

from packages.agent_core.errors import CoreError
from packages.agent_core.injection import ModelGateway
from packages.agent_core.tools.registry import Capability, freeze_registry, register
from packages.contracts.enums import ReasonCode
from packages.contracts.types import JsonObject
from services.control_plane.attachments.csv_processors import (
    csv_aggregate,
    csv_filter,
    csv_parse,
    csv_schema,
    csv_select,
    csv_sort,
    csv_stats,
)
from services.control_plane.attachments.media_processors import (
    MediaResult,
    chunk_text,
    clean_text,
    clip_video,
    crop_image,
    extract_fields,
    frame_at,
    frames_ocr,
    keyframes,
    parse_text,
    probe_video,
    text_stats,
)
from services.control_plane.attachments.real_processors import (
    process_ocr,
    process_video,
    process_visual,
)
from services.control_plane.attachments.vision import understand_image
from services.control_plane.attachments.vision_tools import (
    vision_classify,
    vision_compare,
    vision_describe,
    vision_elements,
    vision_locate,
    vision_ocr,
)
from services.orchestrator.recipe_loader import load_recipe


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


# --- P2 会话查询能力（user_id 从请求 ContextVar 读，args 不含 user_id 防越权）---

class SessionListArgs(BaseModel):
    """列出会话内 widget：只带 session_id（身份从请求上下文读，不进 args）。"""

    session_id: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=200)


class SessionDetailArgs(BaseModel):
    """读取单个 widget 详情。"""

    bundle_id: str = Field(min_length=1)


class SessionAttachmentsArgs(BaseModel):
    """列出会话内附件。"""

    session_id: str = Field(min_length=1)


class SessionQueryPort(Protocol):
    """会话查询端口：user_id 由调用方从请求上下文显式传入（共享服务）。

    P2 #36：从"实例预绑定"改为"方法带 user_id"，因为全局 REGISTRY 的 handler
    是进程级单例，不能持有 per-request 用户。user_id 由 handler 从 ContextVar
    读取后传入，模型无法经 args 伪造（args model 无 user_id 字段）。
    """

    def list_session_widgets(self, user_id: str, session_id: str, limit: int) -> list[JsonObject]: ...
    def get_widget_detail(self, user_id: str, bundle_id: str) -> JsonObject | None: ...
    def list_session_attachments(self, user_id: str, session_id: str) -> list[JsonObject]: ...


def _register_session_capabilities(query: SessionQueryPort) -> None:
    """P2：tool.session.* 三个能力，L0 摘要→L1 schema→L2 执行，与 LLM tools 同源。"""
    from services.orchestrator.request_context import user_id_var

    def _current_user() -> str:
        uid = user_id_var.get()
        # fail-closed：无 per-request 身份绝不执行查询（防越权/串号）。
        if not uid:
            raise CoreError.business(
                ReasonCode.CAPABILITY_DENIED,
                "session capability requires request user context",
            )
        return uid

    def list_widgets(args: BaseModel) -> str:
        a = cast(SessionListArgs, args)
        items = query.list_session_widgets(_current_user(), a.session_id, a.limit)
        return json.dumps({"items": items}, ensure_ascii=False, default=str)

    def get_detail(args: BaseModel) -> str:
        a = cast(SessionDetailArgs, args)
        item = query.get_widget_detail(_current_user(), a.bundle_id)
        return json.dumps({"item": item}, ensure_ascii=False, default=str)

    def list_attachments(args: BaseModel) -> str:
        a = cast(SessionAttachmentsArgs, args)
        items = query.list_session_attachments(_current_user(), a.session_id)
        return json.dumps({"items": items}, ensure_ascii=False, default=str)

    for cid, brief, cost, args_model, handler in (
        ("tool.session.list_widgets", "列出会话内已生成的 widget", 60,
         SessionListArgs, list_widgets),
        ("tool.session.get_widget_detail", "读取某个 widget 的精准信息", 40,
         SessionDetailArgs, get_detail),
        ("tool.session.list_attachments", "列出会话内附件及用途", 50,
         SessionAttachmentsArgs, list_attachments),
    ):
        register(
            cid,
            Capability(
                id=cid, kind="tool", brief=brief,
                detail_ref=f"args.{cid}", payload_ref=f"handler.{cid}",
                surfaces=frozenset({"generation"}), cost_hint=cost,
                args_model=args_model, handler=handler,
            ),
        )


# --- ADR-014 多媒体工具参数与 ports ----------------------------------------


class DerivedWriter(Protocol):
    """派生件写入端口（生产由 S3ObjectStore.put_derived 实现）。"""

    def put_derived(self, source_key: str, name: str, data: bytes) -> str:
        """写派生件并返回带 sha256 的引用。"""
        ...


class SourceArgs(BaseModel):
    """多媒体工具公共入参：原文引用（必带 hash）。"""

    source_ref: str = Field(min_length=1)
    """形如 att/.../original#sha256:<hex> 的原文引用。"""


class ImageCropArgs(SourceArgs):
    box: tuple[float, float, float, float]
    padding: int = 0


class ImageUnderstandArgs(SourceArgs):
    prompt: str = "描述图片中的界面元素"
    want_boxes: bool = False


class VideoProbeArgs(SourceArgs):
    pass


class FrameAtArgs(SourceArgs):
    at_seconds: float = Field(ge=0.0)
    format: str = "webp"


class KeyframesArgs(SourceArgs):
    interval_s: float = Field(gt=0.0, default=1.0)
    limit: int = Field(gt=0, default=3)


class ClipArgs(SourceArgs):
    start_s: float = Field(ge=0.0)
    end_s: float = Field(gt=0.0)


class FramesOcrArgs(SourceArgs):
    times_s: tuple[float, ...]
    min_score: float = Field(ge=0.0, le=1.0, default=0.5)


class TextParseArgs(SourceArgs):
    format: str = "auto"


class TextStatsArgs(SourceArgs):
    pass


class TextCleanArgs(SourceArgs):
    drop_blank_lines: bool = True


class TextChunkArgs(SourceArgs):
    by: str = "paragraph"
    size: int = Field(gt=0, default=800)
    overlap: int = Field(ge=0, default=0)


class TextExtractArgs(SourceArgs):
    fields: tuple[str, ...]


# --- ADR-014 CSV 工具参数 --------------------------------------------------


class CsvParseArgs(SourceArgs):
    delimiter: str = "auto"
    has_header: bool = True
    limit: int = Field(gt=0, default=50)


class CsvSchemaArgs(SourceArgs):
    delimiter: str = "auto"
    has_header: bool = True


class CsvFilterArgs(SourceArgs):
    field: str
    op: str
    value: str
    delimiter: str = "auto"
    has_header: bool = True
    limit: int = Field(gt=0, default=50)


class CsvSortArgs(SourceArgs):
    field: str
    descending: bool = False
    delimiter: str = "auto"
    has_header: bool = True
    limit: int = Field(gt=0, default=50)


class CsvSelectArgs(SourceArgs):
    fields: tuple[str, ...]
    delimiter: str = "auto"
    has_header: bool = True
    limit: int = Field(gt=0, default=50)


class CsvAggregateArgs(SourceArgs):
    op: str
    field: str = ""
    group_by: str = ""
    delimiter: str = "auto"
    has_header: bool = True


class CsvStatsArgs(SourceArgs):
    delimiter: str = "auto"
    has_header: bool = True


# --- ADR-014 视觉模型工具参数 ----------------------------------------------


class VisionDescribeArgs(SourceArgs):
    prompt: str = "描述这张图片的内容"


class VisionElementsArgs(SourceArgs):
    prompt: str = "列出图片中的所有界面元素"


class VisionLocateArgs(SourceArgs):
    target: str


class VisionOcrArgs(SourceArgs):
    prompt: str = "识别图片中的所有文字"


class VisionClassifyArgs(SourceArgs):
    labels: tuple[str, ...]


class VisionCompareArgs(BaseModel):
    source_ref_a: str = Field(min_length=1)
    source_ref_b: str = Field(min_length=1)
    prompt: str = "对比这两张图片（左图与右图）的差异"


def _default_blob_reader(source_ref: str) -> bytes:
    """默认原文读取器：本地路径（开发/测试）；生产由 build_deps 注入 S3 reader。"""
    return Path(source_ref).read_bytes()


class HashVerifiedObjectStore(Protocol):
    """只读原文所需的最小接口（鸭子协议，便于测试替身）。"""

    def get_with_hash_verify(self, key: str, expected_hash: str) -> bytes:
        """按 key 读取并校验 sha256，篡改即抛。"""
        ...


def make_s3_blob_reader(store: HashVerifiedObjectStore) -> Callable[[str], bytes]:
    """把 S3ObjectStore 包装成附件能力用的原文读取器（ADR-003 未完成项4）。

    原文引用形如 ``att/.../original#sha256:<hash>``。读取时拆出 key 与 hash，
    走带校验的读取——篡改或无 hash 一律拒绝（fail-closed，不返回脏原文）。

    Args:
        store: 具备 ``get_with_hash_verify(key, hash) -> bytes`` 的对象存储。

    Returns:
        以 source_ref 为入参、返回原文字节的读取器。
    """

    def reader(source_ref: str) -> bytes:
        key, sep, expected_hash = source_ref.partition("#sha256:")
        if not sep or not expected_hash:
            raise ValueError(f"attachment ref missing sha256 hash: {source_ref!r}")
        return store.get_with_hash_verify(key, expected_hash)

    return reader


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


# --- ADR-014 多媒体 handler 构造 -------------------------------------------


def _media_payload(result: MediaResult, refs: dict[str, str] | None = None) -> str:
    """结果序列化：派生引用按 name 回填，不内联字节。"""
    extracted = dict(result.extracted)
    if refs:
        extracted["derived_refs"] = cast(JsonObject, refs)
    payload = cast(
        JsonObject,
        {
            "handler": result.handler,
            "extracted": extracted,
            "insight_quality": result.insight_quality,
            "degraded": result.degraded,
        },
    )
    return json.dumps(payload, ensure_ascii=False, default=str)


def _make_media_handler(
    reader: Callable[[str], bytes],
    writer: DerivedWriter,
    fn: Callable[..., MediaResult],
    arg_names: tuple[str, ...] = (),
) -> Callable[[BaseModel], str]:
    """把处理函数包装成能力 handler（只从 args 取显式字段）。"""

    def handler(args: BaseModel) -> str:
        source_ref = str(args.model_dump().get("source_ref", ""))
        key, sep, digest = source_ref.partition("#sha256:")
        if not sep or not digest:
            raise ValueError("attachment ref missing sha256 hash")
        raw = reader(source_ref)
        kwargs = {name: getattr(args, name) for name in arg_names}
        result = fn(raw, **kwargs)
        refs: dict[str, str] = {}
        for item in result.derived:
            refs[item.name] = writer.put_derived(key, item.name, item.data)
        return _media_payload(result, refs)

    return handler


def _make_understand_handler(
    reader: Callable[[str], bytes], gateway: ModelGateway | None
) -> Callable[[ImageUnderstandArgs], str]:
    """视觉理解：原文转 data URL 缩略图后复用 understand_image。"""

    def handler(args: ImageUnderstandArgs) -> str:
        raw = reader(args.source_ref)
        import base64
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as img:
            small = img.convert("RGB").copy()
            small.thumbnail((512, 512))
            buf = io.BytesIO()
            small.save(buf, format="WEBP")
        ref = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        prompt = args.prompt
        if args.want_boxes:
            prompt += "\n只输出 JSON：{\"elements\":[{\"text\":字符串,\"bbox\":[x0,y0,x1,y1]}]}。"
        vision = understand_image(ref, prompt, gateway)
        return _media_payload(
            MediaResult(
                "ImageUnderstandProcessor",
                cast(
                    JsonObject,
                    {"elements": list(vision.elements), "want_boxes": args.want_boxes},
                ),
                degraded=vision.degraded,
                insight_quality=0.0 if vision.degraded else 1.0,
            )
        )

    return handler


def _make_vision_compare_handler(
    reader: Callable[[str], bytes], gateway: ModelGateway | None
) -> Callable[[BaseModel], str]:
    """双图对比 handler：分别按 hash 校验读取两张原文再送入视觉工具。"""

    def handler(args: BaseModel) -> str:
        payload = args.model_dump()
        a, b = str(payload.get("source_ref_a", "")), str(payload.get("source_ref_b", ""))
        for ref in (a, b):
            _key, sep, digest = ref.partition("#sha256:")
            if not sep or not digest:
                raise ValueError("attachment ref missing sha256 hash")
        result = vision_compare(
            reader(a), reader(b), str(payload.get("prompt", "")), gateway
        )
        return _media_payload(result)

    return handler


def _inspect_result(raw: bytes) -> MediaResult:
    """图片 inspect：复用 process_visual 确定性提取，剥掉缩略图 base64。"""
    result = process_visual(raw, "memory://inspect")
    extracted = dict(result.extracted)
    extracted.pop("thumbnail_ref", None)
    return MediaResult(
        "ImageInspectProcessor",
        cast(JsonObject, extracted),
        insight_quality=result.insight_quality,
    )


def _ocr_media_result(raw: bytes) -> MediaResult:
    """图片 OCR（ADR-014 工具）：复用 process_ocr 的区域化提取。"""
    result = process_ocr(raw)
    degraded = bool(result.extracted.get("degraded"))
    return MediaResult(
        "ContentExtractProcessor",
        cast(JsonObject, {**result.extracted, "degraded": degraded}),
        insight_quality=result.insight_quality,
        degraded=degraded,
    )


def register_capabilities(
    gateway: ModelGateway | None = None,
    blob_reader: Callable[[str], bytes] | None = None,
    derived_writer: DerivedWriter | None = None,
    *,
    include_code_exec: bool = True,
    include_attachments: bool = True,
    session_query: SessionQueryPort | None = None,
) -> None:
    """注册首批能力并冻结注册表（Q2 composition root）。

    Args:
        gateway: 视觉理解用的 ModelGateway；None 表示当前环境无真实模型（降级）。
        blob_reader: 原文读取器；None 用本地路径默认实现。
        include_code_exec: 是否注册代码执行（隔离机制未落地时生产必须 False，S4）。
        include_attachments: 是否注册附件能力（无 S3 reader 时生产可关，S5）。
        derived_writer: 派生件写入端口；提供时一并注册 ADR-014 的 14 个
            多媒体工具。生产必须注入（build_deps 保证），缺省仅供不测媒体的单测。
        session_query: P2 会话查询端口；提供时注册 tool.session.* 三项能力。
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
    for recipe_id, cost in (
        ("recipe.calendar.month.v3", 220),
        ("recipe.task.list.v2", 180),
        ("recipe.weather.card.v1", 180),
    ):
        register(
            recipe_id,
            Capability(
                id=recipe_id, kind="skill", brief=f"生成规程：{recipe_id}",
                detail_ref="manifest.recipe", payload_ref=f"body.{recipe_id}",
                surfaces=frozenset({"generation"}), cost_hint=cost,
                args_model=RecipeArgs, body=load_recipe(recipe_id).text,
            ),
        )
    # ADR-014：14 个多媒体工具与首批能力在同一函数注册，同一个 freeze 收口。
    if derived_writer is not None:
        _register_media_capabilities(reader, derived_writer, gateway)
    # P2：会话查询能力（同样在 freeze 前，与 LLM tools 同源渐进披露）。
    if session_query is not None:
        _register_session_capabilities(session_query)
    freeze_registry()


def _register_media_capabilities(
    reader: Callable[[str], bytes],
    writer: DerivedWriter,
    gateway: ModelGateway | None,
) -> None:
    """注册 ADR-014 的 14 媒体 + 7 CSV + 6 视觉模型工具（由 register_capabilities 调用）。"""
    specs: tuple[
        tuple[str, str, str, int, type[BaseModel], Callable[[BaseModel], str]], ...
    ] = (
        (
            "tool.image.crop", "按坐标框裁出图片区域", "args.image.crop", 80, ImageCropArgs,
            _make_media_handler(reader, writer, crop_image, ("box", "padding")),
        ),
        (
            "tool.image.ocr", "区域化 OCR 与文字坐标", "args.image.ocr", 90, SourceArgs,
            _make_media_handler(reader, writer, _ocr_media_result),
        ),
        (
            "tool.video.probe", "视频时长/分辨率/帧率", "args.video.probe", 60, VideoProbeArgs,
            _make_media_handler(reader, writer, probe_video),
        ),
        (
            "tool.video.frame_at", "按秒精确取一帧", "args.video.frame_at", 100, FrameAtArgs,
            # frame_at(data, at_seconds, fmt) 的第三参名是 fmt 不是 format；
            # 只转发 at_seconds，format 走函数默认 webp（修 arg_names 转发错误）。
            _make_media_handler(reader, writer, frame_at, ("at_seconds",)),
        ),
        (
            "tool.video.keyframes", "等间隔抽关键帧（带时间戳）", "args.video.keyframes", 120,
            KeyframesArgs,
            _make_media_handler(reader, writer, keyframes, ("interval_s", "limit")),
        ),
        (
            "tool.video.clip", "按时间区间导出短片段", "args.video.clip", 140, ClipArgs,
            _make_media_handler(reader, writer, clip_video, ("start_s", "end_s")),
        ),
        (
            "tool.video.frames_ocr", "指定时刻/关键帧的文字提取", "args.video.frames_ocr", 130,
            FramesOcrArgs,
            _make_media_handler(reader, writer, frames_ocr, ("times_s", "min_score")),
        ),
        (
            "tool.text.parse", "CSV/JSON 严格解析为行", "args.text.parse", 60, TextParseArgs,
            _make_media_handler(reader, writer, parse_text, ("format",)),
        ),
        (
            "tool.text.stats", "字数/行数/编码统计", "args.text.stats", 40, TextStatsArgs,
            _make_media_handler(reader, writer, text_stats),
        ),
        (
            "tool.text.clean", "规范化空白与注入脱敏", "args.text.clean", 50, TextCleanArgs,
            _make_media_handler(reader, writer, clean_text, ("drop_blank_lines",)),
        ),
        (
            "tool.text.chunk", "按字符/段落切片", "args.text.chunk", 70, TextChunkArgs,
            _make_media_handler(reader, writer, chunk_text, ("by", "size", "overlap")),
        ),
        (
            "tool.text.extract_fields", "按列名/JSON 键抽字段", "args.text.extract_fields", 80,
            TextExtractArgs,
            _make_media_handler(reader, writer, extract_fields, ("fields",)),
        ),
        (
            "tool.image.inspect", "图片元信息/主色/尺寸", "args.image.inspect", 60, SourceArgs,
            _make_media_handler(reader, writer, _inspect_result),
        ),
        # --- ADR-014 CSV 工具组（7 个）---
        (
            "tool.csv.parse", "严格解析 CSV 并预览", "args.csv.parse", 60, CsvParseArgs,
            _make_media_handler(reader, writer, csv_parse, ("delimiter", "has_header", "limit")),
        ),
        (
            "tool.csv.schema", "CSV 列类型与空值统计", "args.csv.schema", 70, CsvSchemaArgs,
            _make_media_handler(reader, writer, csv_schema, ("delimiter", "has_header")),
        ),
        (
            "tool.csv.filter", "按条件筛选 CSV 行", "args.csv.filter", 80, CsvFilterArgs,
            _make_media_handler(
                reader, writer, csv_filter,
                ("field", "op", "value", "delimiter", "has_header", "limit"),
            ),
        ),
        (
            "tool.csv.sort", "按列排序 CSV", "args.csv.sort", 80, CsvSortArgs,
            _make_media_handler(
                reader, writer, csv_sort,
                ("field", "descending", "delimiter", "has_header", "limit"),
            ),
        ),
        (
            "tool.csv.select", "CSV 选列投影", "args.csv.select", 70, CsvSelectArgs,
            _make_media_handler(
                reader, writer, csv_select, ("fields", "delimiter", "has_header", "limit")
            ),
        ),
        (
            "tool.csv.aggregate", "CSV 分组聚合", "args.csv.aggregate", 100, CsvAggregateArgs,
            _make_media_handler(
                reader, writer, csv_aggregate,
                ("op", "field", "group_by", "delimiter", "has_header"),
            ),
        ),
        (
            "tool.csv.stats", "CSV 数值列统计", "args.csv.stats", 80, CsvStatsArgs,
            _make_media_handler(reader, writer, csv_stats, ("delimiter", "has_header")),
        ),
        # --- ADR-014 视觉模型工具组（6 个）---
        (
            "tool.vision.describe", "视觉模型描述图片内容", "args.vision.describe", 150,
            VisionDescribeArgs,
            _make_media_handler(
                reader, writer, lambda raw, prompt: vision_describe(raw, prompt, gateway),
                ("prompt",),
            ),
        ),
        (
            "tool.vision.elements", "视觉模型枚举界面元素", "args.vision.elements", 160,
            VisionElementsArgs,
            _make_media_handler(
                reader, writer, lambda raw, prompt: vision_elements(raw, prompt, gateway),
                ("prompt",),
            ),
        ),
        (
            "tool.vision.locate", "视觉模型定位指定对象/get bbox", "args.vision.locate", 160,
            VisionLocateArgs,
            _make_media_handler(
                reader, writer, lambda raw, target: vision_locate(raw, target, gateway),
                ("target",),
            ),
        ),
        (
            "tool.vision.ocr", "视觉模型识别图片文字", "args.vision.ocr", 150, VisionOcrArgs,
            _make_media_handler(
                reader, writer, lambda raw, prompt: vision_ocr(raw, prompt, gateway),
                ("prompt",),
            ),
        ),
        (
            "tool.vision.classify", "视觉模型从候选标签分类", "args.vision.classify", 140,
            VisionClassifyArgs,
            _make_media_handler(
                reader, writer, lambda raw, labels: vision_classify(raw, labels, gateway),
                ("labels",),
            ),
        ),
    )
    for cap_id, brief, detail_ref, cost, args_model, handler in specs:
        register(
            cap_id,
            Capability(
                id=cap_id,
                kind="tool",
                brief=brief,
                detail_ref=detail_ref,
                payload_ref=f"handler.{cap_id}",
                surfaces=frozenset({"generation"}),
                cost_hint=cost,
                args_model=args_model,
                handler=handler,
            ),
        )
    register(
        "tool.image.understand",
        Capability(
            id="tool.image.understand",
            kind="tool",
            brief="图片语义元素与位置描述",
            detail_ref="args.image.understand",
            payload_ref="handler.image.understand",
            surfaces=frozenset({"generation"}),
            cost_hint=150,
            args_model=ImageUnderstandArgs,
            handler=cast(Callable[[BaseModel], str], _make_understand_handler(reader, gateway)),
        ),
    )
    register(
        "tool.vision.compare",
        Capability(
            id="tool.vision.compare",
            kind="tool",
            brief="视觉模型对比两张图片",
            detail_ref="args.vision.compare",
            payload_ref="handler.vision.compare",
            surfaces=frozenset({"generation"}),
            cost_hint=180,
            args_model=VisionCompareArgs,
            handler=_make_vision_compare_handler(reader, gateway),
        ),
    )
