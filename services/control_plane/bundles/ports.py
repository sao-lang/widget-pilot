"""M09 可注入端口（编码规则 8/9：Protocol + 生产 PG/S3 实现，测试桩只在 tests/support）。

purity-exempt-file: 本文件全部为 @runtime_checkable Protocol 接口签名；方法体 `...` 是契约声明
非占位，参数已在各方法 docstring 的 Args 段点名说明（同 memory/ports.py 既有惯例）。

生产实现：BundleStorePort → PgBundleStore；EventSinkPort → M04ArtifactSink；
AuditLedgerPort → PgAuditLedger；PreferenceStorePort → M07 PgPreferenceStore。
端口只定义鸭子协议，不 import 具体实现，避免循环依赖。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.contracts.types import JsonObject
from services.control_plane.bundles.domain import VersionNode
from services.control_plane.bundles.errors import BundleError


@runtime_checkable
class BundleStorePort(Protocol):
    """产物索引 + 版本链持久化（生产 PgBundleStore）。"""

    def insert(self, bundle: JsonObject, versions: tuple[VersionNode, ...]) -> None:
        """写入一个成包记录及首版节点（bundle_id 唯一，重复抛异常）。"""
        ...

    def get(self, bundle_id: str, user_id: str) -> JsonObject | None:
        """按 id + user_id 读取；越权/不存在均返回 None（不泄漏存在性）。"""
        ...

    def list_by_user(
        self, user_id: str, state: str | None, limit: int
    ) -> list[JsonObject]:
        """按用户列包，可选状态过滤。"""
        ...

    def list_by_session(
        self, user_id: str, session_id: str, state: str | None, limit: int
    ) -> list[JsonObject]:
        """按会话列包（租户隔离必带 user_id），最新在前。"""
        ...

    def update_state(self, bundle_id: str, user_id: str, state: str) -> bool:
        """幂等更新状态；返回是否发生实际变更（供审计幂等判定）。"""
        ...

    def replace_versions(
        self, bundle_id: str, user_id: str, versions: tuple[VersionNode, ...]
    ) -> None:
        """覆盖版本链（回滚追加节点后写回）。"""
        ...

    def update_content(
        self,
        bundle_id: str,
        user_id: str,
        bundle: JsonObject,
        versions: tuple[VersionNode, ...],
    ) -> bool:
        """重成包：更新已有 bundle 的可变字段并追加版本链（同一事务）。

        租户隔离按 (bundle_id, user_id) 精确定位；不存在/越权返回 False，
        不创建新行（与 insert 的唯一约束语义区分）。
        """
        ...


@runtime_checkable
class EventSinkPort(Protocol):
    """M04 SSE 事件发射（生产 M04ArtifactSink；artifact/notice/error）。"""

    def emit(self, kind: str, payload: JsonObject, session_id: str = "") -> None:
        """发出一条事件（seq 由实现分配，session_id 定位 Redis seq 域）。"""
        ...


@runtime_checkable
class CorpusLinkPort(Protocol):
    """M10 语料联动（用户删除控件 → 公共语料立即失效，D-1）。"""

    def on_bundle_deleted(self, bundle_id: str) -> None:
        """通知 M10 该包删除，语料按 bundle_id 失效（M09→M10 方向）。"""
        ...


@runtime_checkable
class PreferenceRevocationPort(Protocol):
    """M07 采纳证据精确撤销（M09 删除/回滚调用）。"""

    def revoke_evidence_by_bundle(
        self, user_id: str, bundle_id: str, trace_id: str
    ) -> int:
        """按 bundle_id 撤销该产物的 adopted_artifact 证据，返回撤销条数。"""
        ...


@runtime_checkable
class ArtifactAdoptionPort(Protocol):
    """M07 采纳信号（M09 安装产物时调用，§9.0 义务 1）。"""

    def on_artifact_adopted(
        self,
        user_id: str,
        bundle_id: str,
        palette: str | None,
        recipe_id: str,
        widget_type: str,
        trace_id: str,
    ) -> None:
        """用户采纳/安装产物：写 adopted_artifact 偏好 + 程序记忆采纳计数。

        Args:
            user_id: 租户隔离键。
            bundle_id: 被采纳产物 id（写进证据供删除精确撤销）。
            palette: 产物 WidgetSpec.palette（配色偏好信号）。
            recipe_id: 产物所用规程句柄（程序记忆键）。
            widget_type: 控件类型。
            trace_id: 链路追踪标识。
        """
        ...


def bundle_error_to_http(exc: BundleError) -> tuple[int, str]:
    """把领域异常折叠为 (HTTP 状态码, 业务码)（§5.4 映射，REST 层复用）。"""
    mapping: dict[str, tuple[int, str]] = {
        "INPUT_INVALID": (400, "INPUT_INVALID"),
        "FORBIDDEN": (403, "FORBIDDEN"),
        "NOT_FOUND": (404, "NOT_FOUND"),
        "CONTRACT_MISMATCH": (409, "CONTRACT_MISMATCH"),
    }
    return mapping.get(exc.code, (409, exc.code))
