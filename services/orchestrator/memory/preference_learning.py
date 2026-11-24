"""M07 四类偏好信号的生产写入入口（v1.6，ADR-002 D-10 / D-14）。

把"信号 → 偏好四操作写入 / 程序记忆采纳"的逻辑收敛为可调用的生产函数：
- ``explicit``：由 REST ``PUT /api/users/me/preferences`` 直接调 Store.apply（已有）；
- ``clarification``：澄清回答端点调 :func:`learn_from_clarification`；
- ``adopted_artifact``：M09 安装事件调 :func:`learn_from_adopted_artifact`（M09 未落地前
  由本模块提供入口 + 单测覆盖，触发方接线标注在 M09 §9.0）；
- ``correction``：依赖 M08 纠偏事件，标注未接线（见 M08 §9.0）。

所有函数零 LLM 调用（I-M2）：只从结构化信号写，不读自由文本、不推断。
"""
from __future__ import annotations

from services.orchestrator.memory.ports import (
    PreferenceStorePort,
    ProcedureStorePort,
)
from services.orchestrator.memory.types import (
    PreferenceField,
    PreferenceSource,
    UserPreference,
    UserProcedure,
)


def learn_from_clarification(
    preference_store: PreferenceStorePort,
    user_id: str,
    session_id: str,
    clarified_grid: str | None,
    trace_id: str,
) -> UserPreference | None:
    """从用户澄清点选（grid）学偏好（权重 0.8，D-10 第 2 类信号）。

    Args:
        preference_store: 偏好持久化 Store（生产 PgPreferenceStore）。
        user_id: 租户隔离键。
        session_id: 触发会话（写进情节证据）。
        clarified_grid: 用户点选的规格；None/空表示无结构化偏好信号 → NOOP。
        trace_id: 链路追踪标识。

    Returns:
        写入后的偏好；无信号时返回 None（不写库）。
    """
    if not clarified_grid:
        return None
    pref, _event = preference_store.apply(
        user_id,
        session_id,
        PreferenceField.PREFERRED_GRIDS,
        [clarified_grid],
        PreferenceSource.CLARIFICATION,
        trace_id,
    )
    return pref


def learn_from_adopted_artifact(
    preference_store: PreferenceStorePort,
    procedure_store: ProcedureStorePort | None,
    user_id: str,
    palette: str | None,
    recipe_id: str,
    widget_type: str,
    trace_id: str,
    bundle_ref: str = "",
) -> tuple[UserPreference | None, UserProcedure | None]:
    """从用户采纳/安装产物学偏好与程序记忆（权重 0.3，D-10 第 4 类 + D-14）。

    Args:
        preference_store: 偏好持久化 Store。
        procedure_store: 程序记忆 Store；None 时只学偏好不学程序记忆。
        user_id: 租户隔离键。
        palette: 产物的 WidgetSpec.palette；None 表示无配色信号。
        recipe_id: 产物采用的规程句柄（程序记忆键）。
        widget_type: 产物控件类型。
        trace_id: 链路追踪标识。
        bundle_ref: 被采纳产物 id（M09 §9.0 精确撤销锚点，写入事件供删除/回滚撤销）。

    Returns:
        （写入后的偏好，程序记忆记录）；无对应信号的一侧为 None。
    """
    pref: UserPreference | None = None
    if palette:
        pref, _event = preference_store.apply(
            user_id,
            None,
            PreferenceField.PALETTE,
            palette,
            PreferenceSource.ADOPTED_ARTIFACT,
            trace_id,
            bundle_ref=bundle_ref,
        )
    procedure: UserProcedure | None = None
    if procedure_store is not None and recipe_id:
        procedure = procedure_store.record_adopt(
            user_id, recipe_id, widget_type, trace_id
        )
    return pref, procedure
