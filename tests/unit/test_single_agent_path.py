"""回退验证：多任务下的单 agent 执行路径（ADR-011）。

证明三件事：
1. Dispatcher 不再接受 role_runner 参数（角色层已从接线移除）；
2. 角色层符号已从生产代码移除（无死代码/兼容层）；
3. 任务层符号（fan-out / fan-in / turn）仍完整保留。
"""
from __future__ import annotations

import inspect
from pathlib import Path

from services.orchestrator.dispatcher import Dispatcher


def test_dispatcher_has_no_role_runner_parameter():
    """Dispatcher 构造签名不含 role_runner —— 子任务不再经过角色循环。"""
    params = inspect.signature(Dispatcher.__init__).parameters
    assert "role_runner" not in params, "role_runner 仍可注入，角色层未从接线移除"


def test_role_layer_symbols_removed_from_production():
    """角色层专属符号/文件从生产代码移除（无死代码）。

    基于文件系统全仓扫描（不依赖 git 元数据，CI 打包无 .git 也能跑）。
    """
    root = Path(__file__).resolve().parents[2]
    tasks_dir = root / "services" / "orchestrator" / "tasks"

    assert not (tasks_dir / "roles.py").exists(), "roles.py 仍存在"
    assert not (tasks_dir / "router.py").exists(), "router.py 仍存在"

    forbidden = (
        "run_role_loop", "RoleRunner", "GatewayGenerator", "GatewayReviewer",
        "PgAgentMessageStore", "PgAgentWorkspace", "AgentMessageData",
        "WorkspaceEntry", "WorkspaceConflictError", "MessageType",
        "AgentSessionState", "MAX_AGENT_TURNS", "MAX_REVIEW_ROUNDS",
        "MAX_DELEGATE_DEPTH", "from services.orchestrator.tasks.router",
        "from services.orchestrator.tasks.roles",
    )
    hits: list[str] = []
    for base in ("services", "packages"):
        for py in (root / base).rglob("*.py"):
            if ".venv" in py.parts:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            for symbol in forbidden:
                if symbol in text:
                    hits.append(f"{py.relative_to(root)}:{symbol}")
    assert not hits, f"生产代码仍有角色层符号: {hits}"


def test_task_layer_symbols_still_present():
    """任务层（多任务 fan-out/fan-in + 迭代 turn）未被误删。"""
    from services.orchestrator.tasks.coordinator import fan_in
    from services.orchestrator.tasks.fanout import fanout_from_plan
    from services.orchestrator.tasks.states import TaskState

    assert callable(fan_in)
    assert callable(fanout_from_plan)
    assert TaskState.DONE.value == "DONE"
