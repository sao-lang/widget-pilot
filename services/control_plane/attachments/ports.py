"""M05 附件模块的可注入端口（§3.2 编排 / §8.1 真实实现可插拔）。

内存桩与生产实现（S3ObjectStore / PgAttachmentStore / SessionTenantGuard /
RedisEventSink / RealProcessor）必须共同满足这些结构契约；此前服务层直接
标注内存类，导致生产注入在类型层面被判不兼容（pyright 90+ 处告警的根因之一）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.attachments.stores import AttachmentRow, ObjectRef

if TYPE_CHECKING:
    from packages.agent_core.types import AgentEvent
    from services.control_plane.attachments.service import UploadResult


@runtime_checkable
class ObjectStorePort(Protocol):
    """原文/洞察对象存储：写、按 hash 读、裸读、版本归档。"""

    def put_original(
        self, user_id: str, session_id: str, attachment_id: str, data: bytes
    ) -> ObjectRef: ...

    def put_insight(
        self, user_id: str, session_id: str, attachment_id: str, data: bytes
    ) -> ObjectRef: ...

    def get_with_hash_verify(self, key: str, expected_hash: str) -> bytes: ...

    def get_raw(self, key: str) -> bytes | None: ...

    def versions(self, attachment_id: str) -> list[str]: ...

    def bump_version(self, attachment_id: str, prefix_hint: str = "") -> int: ...

    def archive_insight(self, insight_ref: str, version: int) -> None: ...


@runtime_checkable
class AttachmentStorePort(Protocol):
    """附件索引存储：按会话/用户隔离的 CRUD。"""

    def insert(self, row: AttachmentRow) -> None: ...

    def get(self, attachment_id: str) -> AttachmentRow | None: ...

    def list_by_session(
        self, user_id: str, session_id: str
    ) -> list[AttachmentRow]: ...

    def update(self, row: AttachmentRow) -> None: ...

    def all_rows(self) -> list[AttachmentRow]: ...


@runtime_checkable
class TenantGuardPort(Protocol):
    """会话归属守卫：越权一律抛 PermissionError。"""

    def assert_owner(self, user_id: str, session_id: str) -> None: ...


@runtime_checkable
class AgentEventBusPort(Protocol):
    """M04 事件总线：RedisEventBus 与测试内存总线共同满足。"""

    def emit(self, event: AgentEvent) -> None: ...


@runtime_checkable
class EventSinkPort(Protocol):
    """M05 事件 sink：会话内单调 seq + 事件发射（best-effort 由 emitter 保证）。"""

    def emit(self, event: JsonObject) -> None: ...

    def next_seq(self, session_id: str = "") -> int: ...


@runtime_checkable
class AlertSinkPort(Protocol):
    """拒绝类事件的告警通道（与 EventSinkPort.emit 同形，语义独立）。"""

    def emit(self, event: JsonObject) -> None: ...


@runtime_checkable
class ProcessorOutputPort(Protocol):
    """处理器产出（真实 ProcessorResult 与测试桩共同满足）。

    用只读属性声明：frozen dataclass 的字段不可写，若声明为可写属性会导致
    生产 RealProcessor 在类型层面被误判为不满足协议。
    """

    @property
    def handler(self) -> str: ...

    @property
    def extracted(self) -> JsonValue: ...

    @property
    def insight_quality(self) -> float: ...


@runtime_checkable
class ProcessorPort(Protocol):
    """处理器入口：不可信附件数据只当数据处理，不当指令。"""

    def process(
        self, data: bytes, role: str, kind: str, source_ref: str
    ) -> ProcessorOutputPort: ...


@runtime_checkable
class AttachmentServicePort(Protocol):
    """M05 附件服务门面：REST 路由依赖的最小结构契约（生产/测试同构）。

    只声明 server.py 实际使用的存储属性与两个编排方法；用只读属性声明
    objects/attachments，具体服务的可写实例属性天然满足该结构（规则 3）。
    """

    @property
    def objects(self) -> ObjectStorePort: ...

    @property
    def attachments(self) -> AttachmentStorePort: ...

    def upload(
        self,
        user_id: str,
        session_id: str,
        data: bytes,
        kind: str,
        mime: str,
        description: str,
    ) -> UploadResult: ...

    def patch_roles(
        self, user_id: str, session_id: str, attachment_id: str, new_role: str
    ) -> UploadResult: ...
