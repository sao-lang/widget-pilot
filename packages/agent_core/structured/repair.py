"""M03 校验错误结构化回灌（§11.5 / §4.1 L7）。

把 {field, expected, got} 错误写回 State 并执行 REPAIR 回边：
repair_count += 1（≤MAX_REPAIR）、last_error_fingerprint 更新、信号切回
CALL_MODEL 以便下一轮重新生成。**回边不新增 Run**（§11.3 注释）。
"""
from __future__ import annotations

import hashlib
import json

from packages.agent_core.types import State


def feedback(state: State, errors: list[str]) -> State:
    """执行 REPAIR 回边：计数+1 + 写错误 + 信号回 CALL_MODEL（下轮重生成）。"""
    spec = dict(state.spec or {})
    spec["__signal"] = "CALL_MODEL"   # 回边完成：下一轮回到生成回路，不新增 Run
    spec["__errors"] = list(errors)
    fingerprint = hashlib.sha256(
        json.dumps(errors, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return state.bump(
        spec=spec,
        repair_count=state.repair_count + 1,
        last_error_fingerprint=fingerprint,
    )
