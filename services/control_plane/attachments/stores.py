"""M05 对象存储与附件索引的内存实现（§4.3 store 接口 / §8.1 验收桩）。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from packages.contracts.enums import AttachmentRole


@dataclass(slots=True, frozen=True)
class ObjectRef:
    """对象引用（§4.3 put_original 返回）。"""

    key: str
    hash: str
    size: int


@dataclass(slots=True)
class AttachmentRow:
    """Attachment 实体（§6.1，v1.7 S1 拆双 confidence）。"""

    id: str
    user_id: str
    session_id: str
    kind: str
    mime: str
    size: int
    object_key: str
    hash: str
    role: AttachmentRole | str | None = None
    role_confidence: float = 0.0
    insight_ref: str | None = None
    insight_quality: float = 0.0
    handler: str = ""
    processing_state: str = "RECEIVED"
    clarify_hint: str = ""
    idem_key: str = ""
    evidence_ref: str = ""


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
