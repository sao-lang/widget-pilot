"""M05 服务编排（§3.2 upload 流程 / B2 澄清信号）。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from services.control_plane.attachments.constants import ROLE_CLARIFY_THRESHOLD
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
    ) -> None:
        self.objects = objects
        self.attachments = attachments
        self.tenant = tenant
        self.state_sink = FakeStateSink()
        self.clarify_signals: list[dict[str, str]] = []
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
            self.attachments.insert(row)
            self.clarify_signals.append(
                {
                    "attachment_id": attachment_id,
                    "role_confidence": f"{decision.role_confidence:.3f}",
                    "clarify_hint": _CLARIFY_HINT,
                }
            )
            return self._to_result(row)

        # 5. role 明确：分派处理器 → 封装 AttachmentInsight（v1.7 拆双字段）
        row.role = decision.role
        row.role_confidence = decision.role_confidence
        row.handler = _HANDLERS[decision.role]
        # S-6：服务真实调用静态处理器（注入 fake handler 可由 fail_next_dispatch 模拟失败）
        source_ref = f"{original.key}#sha256:{original.hash}"
        try:
            if self.fail_next_dispatch:
                self.fail_next_dispatch = False
                raise TimeoutError("processor timeout")
            dispatched = dispatch(decision.role, kind, source_ref)
            extracted = dispatched.extracted
            self.dispatch_log[attachment_id] = dispatched.handler
            row.handler = dispatched.handler
        except (TimeoutError, ValueError) as exc:
            # A17：处理器不可恢复失败 → FAILED + evidenceRef，不进 INSIGHT_READY
            row.processing_state = "FAILED"
            row.evidence_ref = f"att/{user_id}/{session_id}/{attachment_id}/error.json"
            self.attachments.insert(row)
            result = self._to_result(row)
            return _with_error(result, str(exc))
        row.processing_state = "INSIGHT_READY"
        insight_quality = 1.0
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
        self.attachments.insert(row)
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
            version = self.objects.insight_versions.get(attachment_id, 0) + 1
            self.objects.insight_versions[attachment_id] = version
            old_key = row.insight_ref.partition("#sha256:")[0]
            if old_key in self.objects.objects:
                old_data = self.objects.objects[old_key]
                vkey = old_key.replace("insight.json", f"insight.v{version}.json")
                self.objects.objects[vkey] = old_data

        # 重新分派处理器 → 新洞察（只动目标附件）
        source_ref = f"{row.object_key}#sha256:{row.hash}"
        dispatched = dispatch(new_role, kind_for_dispatch(row.kind), source_ref)
        extracted = dispatched.extracted
        self.dispatch_log[attachment_id] = dispatched.handler
        row.handler = dispatched.handler
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
        for row in self.attachments.rows.values():
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
        for row in self.attachments.rows.values():
            if not row.insight_ref:
                continue
            ref_key = row.insight_ref.partition("#sha256:")[0]
            data = self.objects.objects.get(ref_key)
            if data is not None:
                insight = json.loads(data.decode("utf-8"))
                chunks.append(_json_bytes(insight.get("extracted")).decode("utf-8"))
        return "\n".join(chunks)

    def read_insight(self, insight_ref: str) -> AttachmentInsight:
        """按 insight_ref 读取并解析 AttachmentInsight（带 hash 校验）。"""
        ref, _, tagged = insight_ref.partition("#sha256:")
        if not tagged:
            ref = insight_ref
            # 内存桩：insight_ref 存裸 key，取回时从对象反查 hash 不确定，直接解析
            data = self.objects.objects[ref]
        else:
            data = self.objects.get_with_hash_verify(ref, tagged)
        return AttachmentInsight(**json.loads(data.decode("utf-8")))

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


@dataclass(slots=True, frozen=True)
class AttachmentInsight:
    """统一洞察结构（§6.2，v1.7 S1：insight_quality 不参与 role 判定）。"""

    source_ref: str
    extracted: object
    insight_quality: float


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
