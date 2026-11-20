"""测试替身（test doubles）：M05 附件模块内存/Fake 实现，生产代码禁止 import。

实现从 services/control_plane/attachments 原样迁移，仅保留中文 docstring 与行为；
数据类/常量/协议（AttachmentRow、ObjectRef、MAX_KEYFRAMES、AlertSinkPort）
从生产模块导入，避免重复定义导致类型漂移。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from packages.agent_core.types import JsonObject, JsonValue
from services.control_plane.attachments.constants import MAX_KEYFRAMES
from services.control_plane.attachments.ports import AlertSinkPort
from services.control_plane.attachments.stores import AttachmentRow, ObjectRef

__all__ = [
    "MAX_KEYFRAMES",
    "AlertSinkPort",
    "AttachmentRow",
    "DispatchProcessor",
    "DispatchResult",
    "FailingProcessor",
    "FakeProcessorResult",
    "FakeStateSink",
    "FakeTenantGuard",
    "InMemoryAttachmentStore",
    "InMemoryEventSink",
    "InMemoryObjectStore",
    "ObjectRef",
    "UnregisteredRoleError",
    "dispatch",
]


class InMemoryObjectStore:
    """内存对象存储：key → bytes，hash 校验，原文与洞察分离前缀。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.insight_versions: dict[str, int] = {}

    def put_original(
        self, user_id: str, session_id: str, attachment_id: str, data: bytes
    ) -> ObjectRef:
        """写原文到 att/*/original，返回带 sha256 的引用。"""
        key = _object_key(user_id, session_id, attachment_id, "original")
        return self._put(key, data)

    def put_insight(
        self, user_id: str, session_id: str, attachment_id: str, data: bytes
    ) -> ObjectRef:
        """写洞察到 att/*/insight.json，返回引用。"""
        key = _object_key(user_id, session_id, attachment_id, "insight.json")
        return self._put(key, data)

    def get_with_hash_verify(self, key: str, expected_hash: str) -> bytes:
        """按 key 读取并校验 hash，篡改即抛。"""
        data = self.objects[key]
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_hash:
            raise ValueError("CONTRACT_MISMATCH: object hash mismatch")
        return data

    def _put(self, key: str, data: bytes) -> ObjectRef:
        """写对象并返回带 hash 的引用（hash 与内容绑定，读时校验）。"""
        digest = hashlib.sha256(data).hexdigest()
        self.objects[key] = data
        return ObjectRef(key=key, hash=digest, size=len(data))

    def versions(self, attachment_id: str) -> list[str]:
        """列出该附件保留的历史洞察版本 key（纠偏作废旧版 v{n}）。"""
        return [
            key
            for key in self.objects
            if f"/{attachment_id}/" in key and "insight.v" in key
        ]

    def get_raw(self, key: str) -> bytes | None:
        """按裸 key 读对象（洞察解析用；不校验 hash）。"""
        return self.objects.get(key)

    def bump_version(self, attachment_id: str, prefix_hint: str = "") -> int:
        """洞察版本号 +1，返回新版本号（纠偏作废旧版）。"""
        version = self.insight_versions.get(attachment_id, 0) + 1
        self.insight_versions[attachment_id] = version
        return version

    def archive_insight(self, insight_ref: str, version: int) -> None:
        """把旧洞察复制为 insight.v{n}.json（只追加，不物理删）。"""
        old_key = insight_ref.partition("#sha256:")[0]
        data = self.objects.get(old_key)
        if data is not None:
            vkey = old_key.replace("insight.json", f"insight.v{version}.json")
            self.objects[vkey] = data

    def tamper(self, key: str) -> None:
        """测试钩子：篡改对象 1 字节（§8.2 A11 故障注入）。"""
        data = self.objects.get(key)
        if data is not None:
            self.objects[key] = data + b"x"


class InMemoryAttachmentStore:
    """内存附件索引：按 id 索引，按 (user_id, session_id) 隔离查询。"""

    def __init__(self) -> None:
        self.rows: dict[str, AttachmentRow] = {}

    def insert(self, row: AttachmentRow) -> None:
        """插入附件行。"""
        self.rows[row.id] = row

    def get(self, attachment_id: str) -> AttachmentRow | None:
        """按 id 取附件行。"""
        return self.rows.get(attachment_id)

    def list_by_session(self, user_id: str, session_id: str) -> list[AttachmentRow]:
        """租户隔离查询：仅返回该用户该会话的附件。"""
        return [
            r
            for r in self.rows.values()
            if r.user_id == user_id and r.session_id == session_id
        ]

    def update(self, row: AttachmentRow) -> None:
        """写回附件行。"""
        self.rows[row.id] = row

    def all_rows(self) -> list[AttachmentRow]:
        """列出全部附件行（红线扫描/统计用）。"""
        return list(self.rows.values())


class FakeTenantGuard:
    """M04 TenantGuard 桩：内存 user_id → session_id 归属表。"""

    def __init__(self, ownership: dict[str, list[str]]) -> None:
        self.ownership = ownership

    def assert_owner(self, user_id: str, session_id: str) -> None:
        """归属不通过抛异常（403 语义），不读文件内容。"""
        if session_id not in self.ownership.get(user_id, []):
            raise PermissionError("FORBIDDEN: session not owned by user")


# 结构性字面量：对象 key 前缀按 §6.5 规范集中一处（编码规则 common §2）
_OBJECT_PREFIX = "att"
_ORIGINAL_SUFFIX = "original"


def _object_key(user_id: str, session_id: str, attachment_id: str, suffix: str) -> str:
    """生成 att/{user_id}/{session_id}/{attachment_id}/{suffix} key（§6.5）。"""
    return f"{_OBJECT_PREFIX}/{user_id}/{session_id}/{attachment_id}/{suffix}"


class FakeStateSink:
    """M04 状态机桩：记录 AWAITING_INPUT 置位调用（B2：本模块不应调用）。"""

    def __init__(self) -> None:
        self.awaiting_calls: list[str] = []

    def mark_awaiting(self, session_id: str) -> None:
        """记录一次置位请求。"""
        self.awaiting_calls.append(session_id)


@dataclass(slots=True)
class InMemoryEventSink:
    """内存事件 sink（测试桩）：记录全部 emit 的事件 dict。"""

    events: list[JsonObject] = field(default_factory=list)
    _seq: int = 0

    def emit(self, event: JsonObject) -> None:
        """记录事件并分配会话内单调 seq。"""
        self.events.append(dict(event))

    def next_seq(self, session_id: str = "") -> int:
        """分配下一个 seq（内存桩：单计数器；生产按 session 分桶）。"""
        self._seq += 1
        return self._seq


class UnregisteredRoleError(Exception):
    """未注册 role：静态白名单拒绝，不动态补注册（A18）。"""


@dataclass(slots=True, frozen=True)
class DispatchResult:
    """处理器分派结果（供验收断言 handler 与 extracted）。"""

    handler: str
    extracted: JsonValue


def dispatch(
    role: str,
    kind: str,
    source_ref: str,
    simulate_timeout: bool = False,
    alert_sink: AlertSinkPort | None = None,
) -> DispatchResult:
    """按 role 查静态表分派处理器。

    Args:
        role: 附件用途枚举值；未注册即拒绝（A18）。
        kind: image/video/text；video 无条件走关键帧处理器、不依赖 role（A15/v1.9）。
        source_ref: 原文或派生件引用。
        simulate_timeout: 测试钩子，模拟处理器超时（A17）。

    Raises:
        UnregisteredRoleError: role 不在静态白名单。
        TimeoutError: simulate_timeout=True。
    """
    registered = frozenset(
        {"visual_reference", "content_extraction", "data_source", "style_reference"}
    )
    # v1.9：视频按 kind 分派、不依赖 role（对齐生产 RealProcessor.process 的
    # `if kind == "video"` 优先分支）——视频无 role 判定时也照常抽关键帧。
    if kind == "video":
        if simulate_timeout:
            raise TimeoutError("processor timeout")
        return DispatchResult(
            "VideoFrameProcessor",
            {"frames": [f"frame_{i:04d}.jpg" for i in range(MAX_KEYFRAMES)]},
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
        cells: list[JsonValue] = []
        for line in lines:
            cells.extend(line.split(","))
        return DispatchResult("DataSourceProcessor", {"cells": cells, "rows": []})
    return DispatchResult(
        "StyleRefProcessor",
        {"palette": [], "radius_hint": 0, "font_scale_hint": 1},
    )


@dataclass(slots=True, frozen=True)
class FakeProcessorResult:
    """测试处理器产出：补齐生产 ProcessorPort 要求的 insight_quality。"""

    handler: str
    extracted: JsonValue
    insight_quality: float = 1.0


class DispatchProcessor:
    """把静态 dispatch 桩适配成 ProcessorPort（记录每次 handler 供断言）。

    Attributes:
        handlers: 按调用顺序记录的 handler 名。
    """

    def __init__(self) -> None:
        self.handlers: list[str] = []

    def process(
        self, data: bytes, role: str, kind: str, source_ref: str
    ) -> FakeProcessorResult:
        """按静态桩分派并返回处理器结果。"""
        result = dispatch(role, kind, source_ref)
        self.handlers.append(result.handler)
        return FakeProcessorResult(result.handler, result.extracted)


class FailingProcessor:
    """前 N 次 process 抛 TimeoutError（替代生产服务里的 fail_next_dispatch 钩子）。

    Attributes:
        _remaining: 剩余失败次数。
        _delegate: 失败次数用尽后的真实桩处理器。
    """

    def __init__(self, fail_times: int = 1, delegate: DispatchProcessor | None = None):
        self._remaining = fail_times
        self._delegate = delegate or DispatchProcessor()

    def process(
        self, data: bytes, role: str, kind: str, source_ref: str
    ) -> FakeProcessorResult:
        """先按次数失败，再退回委托桩。"""
        if self._remaining > 0:
            self._remaining -= 1
            raise TimeoutError("processor timeout")
        return self._delegate.process(data, role, kind, source_ref)


def _derived_base(source_ref: str) -> str:
    """从原文 ref 提取附件目录基路径（去掉 /original 与 #sha256 后缀）。

    派生件（缩略图/关键帧）用独立 key，不继承原文对象名（§6.5）。
    """
    without_hash = source_ref.partition("#sha256:")[0]
    return without_hash.rpartition("/original")[0] or without_hash
