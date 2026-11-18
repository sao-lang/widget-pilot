"""M03 CI 门禁（§11.8 / §11.9 / §8.4）。

GATE OK = 单元测试全绿 + 快照一致 + 端到端冒烟通过。**门禁只断言「已有能力
不退化」**，不因新增能力缺失而阻塞；新缺口登记 backlog（§8.4 #7）。
"""
from __future__ import annotations

from collections.abc import Callable

from packages.agent_core.eval.replay import trajectories_equal
from packages.agent_core.eval.rubric import rubric
from packages.agent_core.types import EvalCase, GateResult, Trajectory


def gate(
    dataset: list[EvalCase],
    replay_fn: Callable[[EvalCase], Trajectory] | None = None,
    baseline: dict[str, Trajectory] | None = None,
) -> GateResult:
    """确定性轨迹断言门禁。

    - 有基线（已有能力）：当前轨迹必须与基线一致，否则 FAIL（退化）。
    - 无基线（新增能力）：缺失不阻塞（backlog），不进 failure。
    - rubric 安全一票否决：safety=0 判 FAIL。
    """
    replay_fn = replay_fn or (lambda c: Trajectory())
    baseline = baseline or {}
    failures: list[str] = []

    for case in dataset:
        # 逐 case 回放，汇总所有失败后一次性返回，便于批量定位退化
        traj = replay_fn(case)
        base = baseline.get(case.case_id)
        if base is not None and not trajectories_equal(traj, base):
            # 有基线却与基线不一致 → 已有能力退化，记为失败
            failures.append(f"{case.case_id}: trajectory regressed vs baseline")
        # 无基线：新增能力缺失 → 登记 backlog，不阻塞
        score = rubric(case, traj)
        if score.dimensions.get("safety", 1.0) == 0.0:
            # safety 为零即一票否决，不论其他维度得分
            failures.append(f"{case.case_id}: safety veto")

    return GateResult(ok=not failures, failures=failures,
                      detail="GATE OK" if not failures else "GATE FAIL")
