"""M03 离线轨迹构建与复现指纹（§11.8 / §4.1 L27）。

从 agent_event 事件重放构建 Trajectory；同题同版本（复现指纹一致）两次跑
轨迹完全一致（T26）。指纹来自 manifest 五要素（§6.4）。
"""
from __future__ import annotations

from packages.agent_core.constants import FINGERPRINT_PARTS
from packages.agent_core.types import (
    AgentEvent,
    FingerprintInfo,
    JsonObject,
    Trajectory,
)


def fingerprint_of(traj: Trajectory) -> tuple[str, ...]:
    """复现指纹的可哈希表示（顺序固定）。"""
    return tuple(traj.fingerprint.get(p, "") for p in FINGERPRINT_PARTS)


def build_trajectory(
    events: list[AgentEvent],
    terminal: JsonObject | None = None,
    fingerprint: FingerprintInfo | None = None,
) -> Trajectory:
    """从事件序列构建确定性轨迹：只保留可断言字段，按 seq 排序。"""
    serialized: list[JsonObject] = [e.to_dict() for e in sorted(events, key=lambda x: x.seq)]
    return Trajectory(events=serialized, terminal=terminal, fingerprint=dict(fingerprint or {}))


def trajectories_equal(a: Trajectory, b: Trajectory) -> bool:
    """两次轨迹深度相等（事件 + 终态 + 指纹）。"""
    return (
        a.events == b.events
        and a.terminal == b.terminal
        and a.fingerprint == b.fingerprint
    )


def replay(events: list[AgentEvent]) -> Trajectory:
    """§4.1 L27：从 agent_event 重放历史会话（便捷入口）。"""
    return build_trajectory(events)
