# Change: f76a7264-rolelayer-rollback / 2026-11-23 / status=confirmed

> 用户拍板（2026-11-23）：**不接受死代码、补丁、兼容层**。U1=删 RoleRunner 死参数；U2=ADR-011；U3=物理删干净（否决"暂留"）；U4=走方案 C。故 Delta/Invariants/Acceptance 已按方案 C 重写——纯角色语义的符号/字段/表/列/测试全删，`turn`（任务层迭代语义）保留。

## Why

用户为一个固定功能的 widget 生成产品硬套了多 agent 角色协作（Generator/Reviewer + Router + 黑板），经反复评估确认该产品不需要多个自主主体，多 agent 是为面试硬凑的。用户决定把"多任务下的多 agent"回退为"多任务下的单 agent"——每个子任务直接跑单 agent 的 run() loop，同时完整保留多任务 fan-out/fan-in 能力。不改会怎样：继续维护一层无真实收益的角色编排，增加状态一致性与冲突收敛的复杂度却无收益。

## Delta

- 删除 `worker.py` 中 `_role_runner` 闭包及其注入（因子任务直接走 `run()` 而消失）
- 删除 `dispatcher.py:820-845` 角色短路分支与 `role_ref`/`role_reason` 局部变量，`result_ref` 改用 `terminal.bundle_ref`（成功）/None，`reason_code` 改用 `terminal.reason_code`（失败）/None
- 删除 `dispatcher.py.__init__` 的 `role_runner` 参数与 `ports.py` 的 `RoleRunner` 协议（U1 拍板，死参数不留）
- 删除 `states.py` 的 `Role`/`MessageType`/`AgentSessionState`/`MAX_AGENT_TURNS`/`MAX_REVIEW_ROUNDS`/`MAX_DELEGATE_DEPTH`（角色常量全删）；同步删除 `planner.py:24` 的 import 与 `:252` 的 `__all__` 导出
- 删除 `ports.py` 的 `AgentMessage`/`ReviewResult`/`GeneratorPort`/`ReviewerPort`；删除 `model_ports.py` 的 `_REVIEW_SYSTEM`/`GatewayGenerator`/`GatewayReviewer`；删除 `events.py` 的 role/agent_id 可选参数传递（保留 turn——任务层迭代语义）
- 删除 `pg_stores.py` 的 `AgentMessageData`/`WorkspaceEntry`/`WorkspaceConflictError`/`PgAgentMessageStore`/`PgAgentWorkspace`；删除 `models_tasks.py` 的 `AgentMessage`/`AgentWorkspace` ORM 与 `GenerationTask.role` 列（U3 删干净）
- 物理删除 `services/orchestrator/tasks/roles.py` 与 `router.py`（无引用）
- 迁移 0018：drop `agent_message`/`agent_workspace` 两表 + drop `generation_task.role` 列（保留 `turn` 列——任务层迭代语义在用）
- 契约：`enums.source.json:11` 删 agent_message/review/delegate 三种 kind → 21 变 18；`events.schema.json:14/31-33` 删对应 kind 枚举与 payload 分支、`:49` 删 `role` 字段、`:51` 删 `agentId` 字段（保留 `:50` `turn`）；`agent_core/types.py:888-893` 删 role/agent_id 字段；`openapi.yaml`/`server.py:348` 删 tasks[] 的 role 字段（保留 turn）；跑四生成物同步
- 删除约 15 个角色层专属测试（planner 4 + sql 7 + pg 4）+ 改 `test_m03_contract.py:22` kind 计数 21→18 + 改清表语句
- 标注 ADR-007 与 docs/代码精读/02 为废弃，新增 ADR-011 记录回退决策（U2 拍板）；006/009/00/整合设计同步

## Invariants

- `dispatcher.py` 的 `run()` 调用链（构造 Input 787-810、run 846-849、task_turn 850-853、终态落库 854-864、fan-in 865-878）不许改变行为
- `coordinator.py` 的 `fan_in`/`emit_fan_in` 与 `planner.py`/`fanout.py`/`PgTaskTurnStore` 不许改动（任务层）
- 删 `states.py` 角色常量时必须同步删 `planner.py` 的 import 与 `__all__` 导出（否则 import 期 NameError）——本轮已删净
- `turn` 语义必须保留：`events.schema.json:50` 的 `turn` 字段、`generation_task.turn` 列、`agent_core/types.py` 的 `turn`、`server.py:350` 的 tasks[].turn、`last_turn_index` 全部不动（ADR-009 迭代语义，非角色层）
- `coordinator.py` 的 docstring 提到 ADR-007 可改写措辞，但 `fan_in`/`emit_fan_in` 的函数签名与行为不许变
- 迁移 0018 的 `down_revision` 必须是 `0017_session_bundle_ref`，幂等守卫用 `get_columns()`（本仓 SQLAlchemy 无 `Inspector.has_column`）
- 契约改动后必须四同步（`pnpm gen:python` / `gen:zod` / `gen:openapi` / `gen:enums`）且 `pnpm contracts:check` drift=[]

## Affected

- `services/orchestrator/worker.py`（接线）
- `services/orchestrator/dispatcher.py`（接线 + result_ref 来源）
- `services/orchestrator/tasks/ports.py`（`RoleRunner` 协议，需 grep 确认无其他消费者后决定是否删）
- `tests/unit/test_m06_multitask_planner.py` / `test_m06_multitask_sql.py` / `tests/integration/test_m06_multitask_pg.py`（删角色层测试）
- `tests/unit/test_m06_dispatcher_fanout_wiring.py`（函数命名/注释提及 role_runner，改文案）
- 设计文档：`docs/adr/ADR-007-多Agent角色协作架构.md`（标注废弃）、`docs/adr/ADR-006-...子任务并发.md`、`docs/adr/ADR-009-...迭代修改.md`、`docs/代码精读/02-任务存储与事件链路.md`（重写为任务存储链路）、`docs/代码精读/01-...md`、`docs/代码精读/05-...md`、`docs/modules/00-总览与模块地图.md`（ADR 索引）、`docs/多Widget任务的多Agent协作架构-整合设计.md`
- 需新增：回退决策 ADR-011

## Acceptance

- AC-1 新增 `test_single_agent_path.py`：无 role_runner 时 dispatcher 经 `run()` 产出终态与会话 done（pytest 通过）
- AC-2 `tests/integration/test_m06_fanout_dispatcher_pg.py` 全通过且不修改（多任务 fan-in 保持）
- AC-3 全仓 grep `run_role_loop`/`Router`/`self._role_runner` 在生产代码零调用（worker/dispatcher 无角色层引用）
- AC-4 dispatcher 子任务失败时 `reason_code` 落 `terminal.reason_code`（不丢失败原因码）
- AC-5 `.venv/Scripts/python.exe -m pytest -q` 全绿（删角色层测试后的基线）
- AC-6 `pnpm contracts:check` → drift=[]（契约 kind 21→18 且四生成物已同步）
- AC-7 `alembic upgrade head` → 0018（drop 两表 + drop generation_task.role 列，保留 turn 列）；downgrade 可回 0017
- AC-8 `.venv/Scripts/python.exe -m ruff check .` 全绿；`npx pyright --pythonpath .venv/Scripts/python.exe` 0 errors
- AC-9 全仓 grep ADR-007 引用处均有废弃标注（历史留痕段除外）

## Open

- 无（U1-U4 已于 2026-11-23 由用户拍板：全删干净 / ADR-011 / 无兼容层 / 方案 C）。本契约 confirmed，可进 S3。
