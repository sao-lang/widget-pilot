"""M09 BundleState 状态机（§3.3 / A12/A13/A14）。

GENERATED → INSTALLED → DELETED 单向；DELETED 终态不可逆。install/delete 幂等，
delete 必须二次确认 token。纯函数：不写库、不写审计——持久化与审计由 BundleService
在同一事务内调用（规则 8：内核无 IO）。
"""
from __future__ import annotations

from collections.abc import Mapping

from packages.contracts.enums import BundleState
from services.control_plane.bundles.errors import (
    BundleConflictError,
    ConfirmRequiredError,
)

_ALLOWED: Mapping[BundleState, frozenset[str]] = {
    BundleState.GENERATED: frozenset({"install", "delete"}),
    BundleState.INSTALLED: frozenset({"install", "delete"}),
    BundleState.DELETED: frozenset(),
}
"""状态 → 允许动作（规则 5：有限取值集合具名映射，DELETED 无出边）。"""


def transition(
    state: BundleState,
    action: str,
    confirm_token: str | None,
) -> BundleState:
    """计算状态迁移后的新状态（纯函数，幂等）。

    Args:
        state: 当前 BundleState。
        action: "install" 或 "delete"（规范化后调用）。
        confirm_token: delete 的二次确认 token；install 忽略。

    Returns:
        迁移后的状态（install 幂等保持 INSTALLED）。

    Raises:
        ConfirmRequiredError: delete 无有效 token。
        BundleConflictError: DELETED 下任何写操作 / 非法动作。
    """
    if action not in _ALLOWED.get(state, frozenset()):
        raise BundleConflictError(f"状态 {state.value} 不接受动作 {action}")
    if action == "install":
        # 幂等：GENERATED 与 INSTALLED 都收敛到 INSTALLED。
        return BundleState.INSTALLED
    # delete：先校验二次确认，再做不可逆迁移。
    if not confirm_token:
        raise ConfirmRequiredError()
    return BundleState.DELETED


def is_writable(state: BundleState) -> bool:
    """判定状态是否接受写操作（DELETED 只读证据）。"""
    return state is not BundleState.DELETED
