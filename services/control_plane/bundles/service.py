"""M09 BundleService 编排层（§3.2 阶段 A/B/C / 规则 10 无功能断点）。

把 M08 校验结论 + M07 源码引用收敛成 WidgetBundle（成包），下发 WidgetRuntime，
驱动 install/delete/rollback 状态机，写只追加审计，发 artifact/notice/error 事件，
联动 M07 采纳信号/撤销与 M10 语料失效。所有依赖显式注入（生产 PG/Redis/S3，
测试桩在 tests/support/），缺配置 fail-fast——源码不保留测试桩。
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from packages.contracts.enums import BundleState, Capability, GridSpec
from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.audit.ledger import AuditLedgerPort
from services.control_plane.bundles.adapter import adapt_validation_to_bundle
from services.control_plane.bundles.confirm import (
    issue_confirm_token,
    verify_confirm_token,
)
from services.control_plane.bundles.domain import VersionNode, WidgetRuntime
from services.control_plane.bundles.errors import (
    BundleConflictError,
    BundleEvidenceError,
    BundleForbiddenError,
)
from services.control_plane.bundles.hash import (
    compute_bundle_hash,
    compute_spec_hash,
)
from services.control_plane.bundles.ports import (
    ArtifactAdoptionPort,
    BundleStorePort,
    CorpusLinkPort,
    EventSinkPort,
    PreferenceRevocationPort,
)
from services.control_plane.bundles.runtime import build_runtime
from services.control_plane.bundles.state import transition
from services.control_plane.bundles.versions import append_version, rollback
from services.guardrails.output_ports import ValidateResult

# 审计动作常量（编码规则 5；ledger.py 的 AuditActions 后续同步扩充）。
_ACTION_ASSEMBLE = "bundle_assemble"
_ACTION_INSTALL = "bundle_install"
_ACTION_DELETE = "bundle_delete"
_ACTION_ROLLBACK = "bundle_rollback"
_DECISION_OK = "OK"
_DECISION_DENY = "FORBIDDEN"


def _nested_str(obj: JsonValue, key: str) -> str:
    """动态边界收窄（规则 6）：从 JSON 值取嵌套对象的字符串字段，非对象/非字符串回退空串。

    bundle 形态来自适配器与 PG JSONB（外部边界），编译/清单等嵌套字段编译期不可静态声明；
    在此一处收窄为 str，下游只做字符串比较，避免 JsonValue 联合类型污染推断。
    """
    if not isinstance(obj, dict):
        return ""
    value = obj.get(key)
    return value if isinstance(value, str) else ""


def _first_palette(spec: JsonValue) -> str | None:
    """从 WidgetSpec.style.palette（string[]）取首个色值作为偏好信号（M09 §9.0）。"""
    if not isinstance(spec, dict):
        return None
    style = spec.get("style")
    if not isinstance(style, dict):
        return None
    palette = style.get("palette")
    if isinstance(palette, list) and palette and isinstance(palette[0], str):
        return palette[0]
    return None


class BundleService:
    """产物与包管理编排（无状态，状态在注入的 store）。"""

    def __init__(
        self,
        *,
        store: BundleStorePort,
        audit: AuditLedgerPort,
        events: EventSinkPort,
        corpus: CorpusLinkPort,
        preference_revocation: PreferenceRevocationPort,
        adoption: ArtifactAdoptionPort,
        confirm_secret: str,
        global_allowlist: frozenset[Capability] | None = None,
    ) -> None:
        """注入全部真实依赖（缺一不可，生产工厂 fail-fast）。

        Args:
            confirm_secret: 删除二次确认 token 的服务端 HMAC 密钥；空串即 fail-fast。
            adoption: M07 采纳信号端口（安装时写 adopted_artifact，必填）。
        """
        if not confirm_secret:
            raise ValueError("confirm_secret 未配置：删除二次确认门禁不可关闭")
        self._store = store
        self._audit = audit
        self._events = events
        self._corpus = corpus
        self._prefs = preference_revocation
        self._adoption = adoption
        self._confirm_secret = confirm_secret
        self._allowlist = global_allowlist

    def _assert_owner(self, bundle: JsonObject | None, user_id: str) -> JsonObject:
        """越权防护：不存在或非本人一律 BundleForbiddenError（不泄漏存在性）。"""
        if bundle is None or bundle.get("user_id") != user_id:
            raise BundleForbiddenError()
        return bundle

    def _audit_write(
        self,
        *,
        user_id: str,
        session_id: str,
        action: str,
        decision: str,
        trace_id: str,
        payload: Mapping[str, str | int | bool | None],
    ) -> None:
        """写只追加审计（payload 禁含 PII / 代码原文）。"""
        self._audit.append(
            user_id, session_id, action, decision, dict(payload), trace_id
        )

    def assemble(
        self,
        *,
        validation: ValidateResult,
        spec: JsonObject,
        source_ref: str,
        user_id: str,
        session_id: str,
        run_id: str,
        trace_id: str = "",
    ) -> JsonObject:
        """成包（step11 / A1）：M08 passed 才落索引 + 首版 + 审计 + artifact 事件。

        Args:
            validation: M08 ValidateResult（生产由编排注入）。
            spec: WidgetSpec（M07 产出，只读）。
            source_ref: widget.tsx 源码对象引用（带 hash）。
            user_id: 租户隔离键。
            session_id: 所属会话。
            run_id: 执行记录标识。
            trace_id: 链路追踪标识。

        Returns:
            落库后的 WidgetBundle（含 hash / state=GENERATED / versions）。
        """
        # 适配器折叠 M08 结论（不改结论、不重测）。
        bundle = adapt_validation_to_bundle(
            validation=validation,
            bundle_id=uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            spec=spec,
        )
        # 源码引用纳入包（双存之一，M07 产出）。
        src = bundle["source"]
        if isinstance(src, dict):
            src["content_ref"] = source_ref
        # manifest 补全完整字段（§6.2）：spec_hash 来自折叠，其余由 spec/model 推导。
        self._fill_manifest(bundle, spec)
        # hash 链：整包 hash = sha256(canonical_json(bundle_without_hash))。
        bundle["hash"] = compute_bundle_hash(bundle)
        bundle["state"] = BundleState.GENERATED.value
        bundle["created_at"] = datetime.now(UTC).isoformat()
        # 首版版本链。
        versions: tuple[VersionNode, ...] = append_version(
            (),
            _nested_str(bundle["compiled"], "content_ref"),
            _nested_str(bundle["compiled"], "hash"),
            _nested_str(bundle["manifest"], "spec_hash"),
            bundle_id=str(bundle["bundle_id"]),
        )
        self._store.insert(bundle, versions)
        bundle["versions"] = [self._version_json(v) for v in versions]
        # 只追加审计。
        self._audit_write(
            user_id=user_id,
            session_id=session_id,
            action=_ACTION_ASSEMBLE,
            decision=_DECISION_OK,
            trace_id=trace_id,
            payload={"bundle_id": str(bundle["bundle_id"]), "run_id": run_id},
        )
        # artifact 事件（payload 符合 events.schema.json：bundle_id + preview_ref）。
        grid_value = str(spec.get("grid", GridSpec._2X2.value))
        self._events.emit(
            "artifact",
            {
                "bundle_id": str(bundle["bundle_id"]),
                "preview_ref": _nested_str(bundle["compiled"], "content_ref"),
                "grid": grid_value,
                "state": BundleState.GENERATED.value,
                "hash": str(bundle["hash"]),
            },
            session_id,
        )
        return bundle

    def _fill_manifest(self, bundle: JsonObject, spec: JsonObject) -> None:
        """补全 manifest 完整字段（§6.2 / schema required 13 项）。"""
        manifest = bundle.get("manifest")
        if not isinstance(manifest, dict):
            manifest = {}
            bundle["manifest"] = manifest
        spec_hash = manifest.get("spec_hash") or compute_spec_hash(spec)
        grid = spec.get("grid", GridSpec._2X2.value)
        widget_type = spec.get("widget_type", "custom")
        declared_caps = spec.get("capabilities", [])
        import_allowlist = spec.get("import_allowlist", ["react", "@widget/bridge"])
        props = spec.get("props_contract", ["grid", "theme", "locale", "tick", "data"])
        manifest.update(
            {
                "spec_version": str(spec.get("spec_version", "v1")),
                "spec_hash": str(spec_hash),
                "grid": grid,
                "widget_type": widget_type,
                "capabilities": declared_caps,
                "import_allowlist": import_allowlist,
                "exports": {"default": "Widget"},
                "props": props,
                "recipe_version": str(spec.get("recipe_version", "v1")),
                "retrieval_snapshot": str(spec.get("retrieval_snapshot", "")),
                "model": str(spec.get("model", "unknown")),
                "prompt_version": str(spec.get("prompt_version", "v1")),
                "model_tier": str(spec.get("model_tier", "standard")),
            }
        )

    def _version_json(self, node: VersionNode) -> JsonValue:
        """版本节点 → JSON（供 REST 下发）。"""
        return {
            "version_id": node.version_id,
            "parent_id": node.parent_id or "",
            "compiled_ref": node.compiled_ref,
            "compiled_hash": node.compiled_hash,
            "manifest_hash": node.manifest_hash,
            "created_at": node.created_at,
        }

    def issue_delete_confirm(
        self, bundle_id: str, user_id: str
    ) -> tuple[str, str]:
        """签发删除二次确认 token（§4.1 Q4）：先校验归属，再签发绑定 token。

        Returns:
            (confirm_token, expires_at)；token 绑定 user_id + bundle_id，TTL 120s。
        """
        self._assert_owner(self._store.get(bundle_id, user_id), user_id)
        return issue_confirm_token(
            secret=self._confirm_secret, user_id=user_id, bundle_id=bundle_id
        )

    def get(self, bundle_id: str, user_id: str) -> JsonObject:
        """只读单包（越权 403）。"""
        return self._assert_owner(self._store.get(bundle_id, user_id), user_id)

    def list_bundles(
        self, user_id: str, state: str | None, limit: int
    ) -> list[JsonObject]:
        """列包（按 user_id 隔离）。"""
        return self._store.list_by_user(user_id, state, limit)

    def install(self, bundle_id: str, user_id: str, trace_id: str = "") -> JsonObject:
        """安装（A12 幂等）：GENERATED/INSTALLED → INSTALLED，写审计。"""
        bundle = self._assert_owner(self._store.get(bundle_id, user_id), user_id)
        session_id = str(bundle["session_id"])
        state = BundleState(str(bundle["state"]))
        new_state = transition(state, "install", None)
        changed = self._store.update_state(bundle_id, user_id, new_state.value)
        if changed:
            self._audit_write(
                user_id=user_id,
                session_id=session_id,
                action=_ACTION_INSTALL,
                decision=_DECISION_OK,
                trace_id=trace_id,
                payload={"bundle_id": bundle_id},
            )
            # M07 §9.0 义务 1：采纳/安装产物发 adopted_artifact 信号（携带 palette）。
            # 仅在首次 install（changed=True）发一次，重复安装不重复写偏好。
            spec = bundle.get("spec")
            manifest = bundle.get("manifest")
            palette = _first_palette(spec)
            recipe_id = _nested_str(manifest, "recipe_version")
            widget_type = _nested_str(manifest, "widget_type")
            self._adoption.on_artifact_adopted(
                user_id, bundle_id, palette, recipe_id, widget_type, trace_id
            )
        bundle["state"] = new_state.value
        return bundle

    def delete(
        self,
        bundle_id: str,
        user_id: str,
        confirm_token: str | None,
        trace_id: str = "",
    ) -> JsonObject:
        """删除（A13/A14）：二次确认 + 不可逆 + 不乐观更新 + 审计 + 联动撤销。"""
        bundle = self._assert_owner(self._store.get(bundle_id, user_id), user_id)
        session_id = str(bundle["session_id"])
        state = BundleState(str(bundle["state"]))
        # 真校验二次确认 token（验签 + 匹配 user/bundle + 未过期）；失败抛 ConfirmRequiredError。
        # 审计只记真实校验结果，绝不写「verified」而实际未校验。
        verify_confirm_token(
            secret=self._confirm_secret,
            token=confirm_token or "",
            user_id=user_id,
            bundle_id=bundle_id,
        )
        new_state = transition(state, "delete", confirm_token)
        self._store.update_state(bundle_id, user_id, new_state.value)
        self._audit_write(
            user_id=user_id,
            session_id=session_id,
            action=_ACTION_DELETE,
            decision=_DECISION_OK,
            trace_id=trace_id,
            payload={"bundle_id": bundle_id, "confirm": "verified"},
        )
        # M07 撤销采纳证据（按 bundle_id 精确）+ M10 语料联动（M09→M10 方向）。
        self._prefs.revoke_evidence_by_bundle(user_id, bundle_id, trace_id)
        self._corpus.on_bundle_deleted(bundle_id)
        bundle["state"] = new_state.value
        return bundle

    def rollback(
        self,
        bundle_id: str,
        user_id: str,
        target_version_id: str | None,
        trace_id: str = "",
    ) -> JsonObject:
        """回滚（A15）：沿用上一版编译产物，追加版本节点，不改 BundleState。"""
        bundle = self._assert_owner(self._store.get(bundle_id, user_id), user_id)
        session_id = str(bundle["session_id"])
        state = BundleState(str(bundle["state"]))
        if state is BundleState.DELETED:
            raise BundleConflictError()
        # versions 是顶层数组字段，显式收窄为 list（规则 6 边界收窄）。
        versions_value = bundle.get("versions")
        raw_versions = versions_value if isinstance(versions_value, list) else []
        versions = tuple(
            VersionNode(
                version_id=str(v.get("version_id", "")),
                bundle_id=bundle_id,
                parent_id=(str(v.get("parent_id")) if v.get("parent_id") else None),
                compiled_ref=str(v.get("compiled_ref", "")),
                compiled_hash=str(v.get("compiled_hash", "")),
                manifest_hash=str(v.get("manifest_hash", "")),
                created_at=str(v.get("created_at", "")),
            )
            for v in raw_versions
            if isinstance(v, dict)
        )
        new_versions = rollback(versions, target_version_id)
        self._store.replace_versions(bundle_id, user_id, new_versions)
        self._audit_write(
            user_id=user_id,
            session_id=session_id,
            action=_ACTION_ROLLBACK,
            decision=_DECISION_OK,
            trace_id=trace_id,
            payload={
                "bundle_id": bundle_id,
                "target": target_version_id or "previous",
            },
        )
        self._prefs.revoke_evidence_by_bundle(user_id, bundle_id, trace_id)
        bundle["versions"] = [self._version_json(v) for v in new_versions]
        return bundle

    def preview(self, bundle_id: str, user_id: str) -> JsonObject:
        """预览（R15）：返回 srcdoc_ref + WidgetRuntime。"""
        bundle = self._assert_owner(self._store.get(bundle_id, user_id), user_id)
        manifest = bundle.get("manifest")
        if not isinstance(manifest, dict):
            raise BundleEvidenceError("manifest 缺失")
        grid_raw = str(manifest.get("grid", GridSpec._2X2.value))
        try:
            grid = GridSpec(grid_raw)
        except ValueError:
            grid = GridSpec._2X2
        namespace = f"{bundle['user_id']}:{bundle_id}"
        runtime: WidgetRuntime = build_runtime(
            grid=grid,
            srcdoc_ref=_nested_str(bundle["compiled"], "content_ref"),
            manifest=manifest,
            storage_namespace=namespace,
            global_allowlist=self._allowlist,
        )
        return {"srcdoc_ref": _nested_str(bundle["compiled"], "content_ref"), "runtime": runtime.to_json()}

    def evidence(self, bundle_id: str, user_id: str) -> JsonObject:
        """证据聚合（A3/I14）：spec/manifest/static_check/sandbox_test/trace 五类齐全。"""
        bundle = self._assert_owner(self._store.get(bundle_id, user_id), user_id)
        return {
            "spec": bundle.get("spec"),
            "manifest": bundle.get("manifest"),
            "static_check": bundle.get("static_check"),
            "sandbox_test": bundle.get("sandbox_test"),
            "trace": bundle.get("evidence_ref", ""),
        }
