"""M05 服务编排（§3.2 upload 流程 / B2 澄清信号）。"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from services.control_plane.attachments.constants import ROLE_CLARIFY_THRESHOLD
from services.control_plane.attachments.events import (
    AttachmentEventEmitter,
    InMemoryEventSink,
)
from services.control_plane.attachments.processors import dispatch
from services.control_plane.attachments.role_resolver import resolve_role
from services.control_plane.attachments.sniff import sniff_mime
from services.control_plane.attachments.stores import (
    AttachmentRow,
    FakeStateSink,
    FakeTenantGuard,
    InMemoryAttachmentStore,
    InMemoryObjectStore,
)

# role → 处理器名（§4.3 registry 静态表；本模块用命名字符串，库级注册归 M03）
_HANDLERS: dict[str, str] = {
    "visual_reference": "VisualRefProcessor",
    "content_extraction": "ContentExtractProcessor",
    "data_source": "DataSourceProcessor",
    "style_reference": "StyleRefProcessor",
}

_CLARIFY_HINT = "请说明这张图是当背景、提取内容、作为数据源还是风格参考"


@dataclass(slots=True, frozen=True)
class UploadResult:
    """上传结果（§6.2 AttachmentUploadResult，v1.7 10 字段）。"""

    attachment_id: str
    kind: str
    mime: str
    size: int
    object_key: str
    hash: str
    role: str | None
    role_confidence: float
    insight_ref: str | None
    insight_quality: float
    handler: str = ""
    clarify_hint: str = ""
    processing_state: str = ""
    evidence_ref: str = ""


class AttachmentService:
    """附件上传 / role 判定 / 纠偏重跑的编排服务。"""

    def __init__(
        self,
        objects: InMemoryObjectStore,
        attachments: InMemoryAttachmentStore,
        tenant: FakeTenantGuard,
        processor: object | None = None,
        event_sink: object | None = None,
    ) -> None:
        """注入存储/租户守卫；processor 为可选真实处理器（§8.1 fake 默认）。

        Args:
            processor: 提供 process(data, role, kind, source_ref) -> ProcessorResult
                的对象；None 时用静态桩 dispatch（独立提测路径）。
        """
        self.objects = objects
        self.attachments = attachments
        self.tenant = tenant
        self.processor = processor
        self.state_sink = FakeStateSink()
        self.clarify_signals: list[dict[str, str]] = []
        self.events = AttachmentEventEmitter(event_sink or InMemoryEventSink())
        self.dispatch_log: dict[str, str] = {}
        self.fail_next_dispatch: bool = False

    def upload(
        self,
        user_id: str,
        session_id: str,
        data: bytes,
        kind: str,
        mime: str,
        description: str,
    ) -> UploadResult:
        """上传编排：租户校验 → 传输校验 → 存储 → role 判定 → 处理器 → 洞察。

        B2 定稿：role 歧义时只产出澄清信号，不调用 M04 置 AWAITING_INPUT。
        """
        # 1. 租户归属先于任何文件读取（A12：越权不读内容）
        self.tenant.assert_owner(user_id, session_id)
        # 2. 幂等：同 session + 同内容 + 同描述 → 复用既有 attachment_id（A13）
        idem_key = f"{session_id}:{description}:{_content_hash(data)}"
        existing = self._find_by_idem(user_id, session_id, idem_key)
        if existing is not None:
            return self._to_result(existing)

        # 3. 写原文（传输校验在真实 API 边界由 validate_transfer 完成；
        #    纯函数已单测覆盖 A3–A6，本服务层聚焦编排）
        attachment_id = uuid.uuid4().hex
        original = self.objects.put_original(user_id, session_id, attachment_id, data)
        sniffed = sniff_mime(data[:16])
        decision = resolve_role(description, kind)

        row = AttachmentRow(
            id=attachment_id,
            user_id=user_id,
            session_id=session_id,
            kind=kind,
            mime=sniffed,
            size=len(data),
            object_key=original.key,
            hash=original.hash,
        )
        row.processing_state = "STORED"
        row.idem_key = idem_key

        # 4. role 歧义：不落默认 role、不生成 insight、不置会话状态（B2）
        if decision.role is None or decision.role_confidence < ROLE_CLARIFY_THRESHOLD:
            row.role = None
            row.role_confidence = decision.role_confidence
            row.processing_state = "CLARIFICATION_NEEDED"
            row.clarify_hint = _CLARIFY_HINT
            row = self._insert_or_reuse(row, user_id, session_id)
            self.clarify_signals.append(
                {
                    "attachment_id": attachment_id,
                    "role_confidence": f"{decision.role_confidence:.3f}",
                    "clarify_hint": _CLARIFY_HINT,
                }
            )
            self.events.emit_notice(session_id, attachment_id, _CLARIFY_HINT)
            return self._to_result(row)

        # 5. role 明确：分派处理器 → 封装 AttachmentInsight（v1.7 拆双字段）
        row.role = decision.role
        row.role_confidence = decision.role_confidence
        row.handler = _HANDLERS[decision.role]
        # S-6：优先用注入的真实处理器（生产）；无注入时用静态桩 dispatch（单测）
        source_ref = f"{original.key}#sha256:{original.hash}"
        insight_quality = 1.0
        try:
            if self.fail_next_dispatch:
                self.fail_next_dispatch = False
                raise TimeoutError("processor timeout")
            if self.processor is not None:
                processed = self.processor.process(
                    data, decision.role, kind, source_ref
                )
                extracted = processed.extracted
                insight_quality = processed.insight_quality
                handler_name = processed.handler
            else:
                dispatched = dispatch(decision.role, kind, source_ref)
                extracted = dispatched.extracted
                handler_name = dispatched.handler
            self.dispatch_log[attachment_id] = handler_name
            row.handler = handler_name
        except (TimeoutError, subprocess.TimeoutExpired, ValueError) as exc:
            # A17：subprocess.TimeoutExpired 不是 TimeoutError 子类，必须显式捕获；
            # 失败证据真实写入对象存储（N11：evidenceRef 不能悬空）。
            row.processing_state = "FAILED"
            error_obj = self.objects.put_insight(
                user_id,
                session_id,
                attachment_id,
                _json_bytes({"error": str(exc), "handler": row.handler}),
            )
            row.evidence_ref = f"{error_obj.key}#sha256:{error_obj.hash}"
            row = self._insert_or_reuse(row, user_id, session_id)
            return self._to_result(row)
        row.processing_state = "INSIGHT_READY"
        insight = {
            "source_ref": source_ref,
            "extracted": extracted,
            "insight_quality": insight_quality,
        }
        insight_ref_obj = self.objects.put_insight(
            user_id, session_id, attachment_id, _json_bytes(insight)
        )
        # 引用带内容 hash（全局不变量）：内容变 → ref 变，A9 纠偏可断言
        row.insight_ref = f"{insight_ref_obj.key}#sha256:{insight_ref_obj.hash}"
        row.insight_quality = insight_quality
        row = self._insert_or_reuse(row, user_id, session_id)
        self.events.emit_attachment(
            session_id, attachment_id, decision.role, "UPLOAD", row.insight_ref
        )
        return self._to_result(row)

    def patch_roles(
        self, user_id: str, session_id: str, attachment_id: str, new_role: str
    ) -> UploadResult:
        """PATCH 纠偏：从 ROLE_ASSIGNED 重跑，只影响目标附件（A8/A9）。

        S3 定稿：CLARIFICATION_NEEDED 与 INSIGHT_READY 两个入口态都以
        ROLE_ASSIGNED 为起点；旧洞察保留为 insight.v{n}.json（只追加审计）。
        """
        self.tenant.assert_owner(user_id, session_id)
        row = self.attachments.get(attachment_id)
        if row is None or row.user_id != user_id or row.session_id != session_id:
            raise PermissionError("FORBIDDEN: attachment not found")
        if new_role not in _HANDLERS:
            raise ValueError(f"INPUT_INVALID: unknown role {new_role}")

        # S-2：同 role 且已 INSIGHT_READY → 幂等返回，不重跑、不版本化
        if row.role == new_role and row.processing_state == "INSIGHT_READY":
            return self._to_result(row)

        # 旧洞察版本化保留（A9：不物理删）
        if row.insight_ref:
            # 前缀提示 = 旧洞察 key 去掉末尾文件名；S3 实现据此精确 list。
            old_key = row.insight_ref.partition("#sha256:")[0]
            prefix_hint = old_key.rpartition("/")[0] + "/"
            version = self.objects.bump_version(attachment_id, prefix_hint)
            self.objects.archive_insight(row.insight_ref, version)

        # 重新分派处理器 → 新洞察（只动目标附件）
        source_ref = f"{row.object_key}#sha256:{row.hash}"
        # N2：纠偏重跑必须走注入的真实处理器（生产），与 upload 同路径
        if self.processor is not None:
            processed = self.processor.process(
                self.objects.get_raw(row.object_key) or b"",
                new_role,
                kind_for_dispatch(row.kind),
                source_ref,
            )
            extracted = processed.extracted
            handler_name = processed.handler
        else:
            dispatched = dispatch(new_role, kind_for_dispatch(row.kind), source_ref)
            extracted = dispatched.extracted
            handler_name = dispatched.handler
        self.dispatch_log[attachment_id] = handler_name
        row.handler = handler_name
        insight = {
            "source_ref": source_ref,
            "extracted": extracted,
            "insight_quality": 1.0,
        }
        new_ref = self.objects.put_insight(
            user_id, session_id, attachment_id, _json_bytes(insight)
        )
        row.role = new_role
        row.role_confidence = 1.0
        row.insight_ref = f"{new_ref.key}#sha256:{new_ref.hash}"
        row.insight_quality = 1.0
        row.processing_state = "INSIGHT_READY"
        row.clarify_hint = ""
        self.attachments.update(row)
        self.events.emit_attachment(
            session_id, attachment_id, new_role, "PATCH", row.insight_ref
        )
        return self._to_result(row)

    def get_attachment(
        self, user_id: str, session_id: str, attachment_id: str
    ) -> AttachmentRow:
        """按 id 取附件（租户隔离，越权抛 PermissionError）。"""
        self.tenant.assert_owner(user_id, session_id)
        row = self.attachments.get(attachment_id)
        if row is None or row.user_id != user_id or row.session_id != session_id:
            raise PermissionError("FORBIDDEN: attachment not found")
        return row

    def external_refs(self) -> list[str]:
        """列出所有对外可见引用（供 A10 红线扫描：不含 original）。"""
        refs: list[str] = []
        for row in self.attachments.all_rows():
            if row.insight_ref:
                refs.append(row.insight_ref)
            if row.object_key and "/original" not in row.object_key:
                refs.append(row.object_key)
        return refs

    def serialize_extracted(self) -> str:
        """S-5：序列化全部洞察的 extracted 段，供原文红线真扫描（非重言式）。

        source_ref 按设计合法指向原文用于溯源；红线针对随产物/语料外流的
        extracted 派生引用，故只取该段并递归覆盖其内嵌字符串。
        """
        chunks: list[str] = []
        for row in self.attachments.all_rows():
            if not row.insight_ref:
                continue
            ref_key = row.insight_ref.partition("#sha256:")[0]
            data = self.objects.get_raw(ref_key)
            if data is not None:
                insight = json.loads(data.decode("utf-8"))
                chunks.append(_json_bytes(insight.get("extracted")).decode("utf-8"))
        return "\n".join(chunks)

    def read_insight(self, insight_ref: str) -> AttachmentInsight:
        """按 insight_ref 读取并解析 AttachmentInsight（带 hash 校验）。"""
        ref, _, tagged = insight_ref.partition("#sha256:")
        if not tagged:
            ref = insight_ref
            # 裸 key：直接读取（生产 ref 必带 hash；此分支仅兼容历史数据）
            data = self.objects.get_raw(ref)
        else:
            data = self.objects.get_with_hash_verify(ref, tagged)
        return AttachmentInsight(**json.loads(data.decode("utf-8")))

    def _insert_or_reuse(
        self, row: AttachmentRow, user_id: str, session_id: str
    ) -> AttachmentRow:
        """N4：并发同幂等键时 INSERT 撞唯一索引 → 折叠为复用既有行。

        返回实际落库的行（自己插入的 row，或并发胜出者的既有行）。
        调用方若拿到既有行，应丢弃本次新写的 S3 原文（孤儿对象清理待 M11）。
        """
        try:
            self.attachments.insert(row)
            return row
        except Exception as exc:
            # 只折叠唯一键冲突；其他异常照常抛出
            if "UNIQUE" not in str(exc).upper() and "DUPLICATE" not in str(exc).upper():
                raise
            # R-1：FAILED/REJECTED 行被 `_find_by_idem` 跳过，但唯一索引仍在。
            # 此时不是"重试复用旧结果"，而是"重试应重新跑"——把本次新状态写回
            # 既有行（保留 id，更新 object_key/hash/状态），避免永久 500。
            existing = self._find_by_idem(user_id, session_id, row.idem_key)
            if existing is None:
                existing = next(
                    (
                        r
                        for r in self.attachments.list_by_session(user_id, session_id)
                        if r.idem_key == row.idem_key
                    ),
                    None,
                )
            if existing is None:
                raise
            if existing.processing_state in ("FAILED", "REJECTED"):
                from dataclasses import fields, replace

                refreshed = replace(
                    existing,
                    **{
                        f.name: getattr(row, f.name)
                        for f in fields(row)
                        if f.name not in ("id", "idem_key", "user_id", "session_id")
                    },
                )
                self.attachments.update(refreshed)
                return refreshed
            return existing

    def _find_by_idem(
        self, user_id: str, session_id: str, idem_key: str
    ) -> AttachmentRow | None:
        """按幂等键查既有附件（内存实现：内容/描述同则可复用的行）。"""
        for row in self.attachments.list_by_session(user_id, session_id):
            # R-1：FAILED/REJECTED 是终态旁路，不得被幂等键复用——否则同输入重试
            # 永远拿回失败行，无法恢复（§4.5「校验拒绝是终态，基础设施故障可重试」）
            if row.idem_key == idem_key and row.processing_state not in (
                "FAILED",
                "REJECTED",
            ):
                return row
        return None

    def _to_result(self, row: AttachmentRow) -> UploadResult:
        """附件行 → 上传响应（10 字段，v1.7 B3）。"""
        return UploadResult(
            attachment_id=row.id,
            kind=row.kind,
            mime=row.mime,
            size=row.size,
            object_key=row.object_key,
            hash=row.hash,
            role=row.role if isinstance(row.role, str) else None,
            role_confidence=row.role_confidence,
            insight_ref=row.insight_ref,
            insight_quality=row.insight_quality,
            handler=row.handler,
            clarify_hint=row.clarify_hint,
            processing_state=row.processing_state,
            evidence_ref=row.evidence_ref,
        )


class AttachmentInsight(BaseModel):
    """统一洞察结构（§6.2；A14：Pydantic 严格校验）。

    source_ref 必带 hash；insight_quality 限定 [0,1]；extracted 为结构化数据。
    v1.7 S1：insight_quality 不参与 role 判定。
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    source_ref: str
    extracted: object
    insight_quality: float = Field(ge=0.0, le=1.0)


def _content_hash(data: bytes) -> str:
    """计算内容摘要用于幂等键。"""
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: object) -> bytes:
    """序列化洞察为 UTF-8 JSON（确定性排序，便于快照断言）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")


def kind_for_dispatch(kind: str) -> str:
    """附件行 kind → dispatch 的 kind 参数（防御非法枚举，回退 image）。"""
    return kind if kind in ("image", "video", "text") else "image"


def _with_error(result: UploadResult, detail: str) -> UploadResult:
    """附加失败判据到结果（保留 dataclass 不可变，用 replace）。"""
    from dataclasses import replace

    return replace(result, evidence_ref=f"{result.evidence_ref}?detail={detail}")
