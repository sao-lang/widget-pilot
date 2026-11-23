# 多任务下单 agent 回退 · 设计规格

**状态**: 已确认（2026-11-23 用户拍板方案 C：全删干净，无死代码/兼容层）
**范围**: 把当前"多任务下每个子任务跑多 agent 角色协作"回退为"多任务下每个子任务直接跑单 agent run()"，保留多任务 fan-out/fan-in
**技术栈摘要**: Python 3.x / asyncio + Redis Streams consumer group / PostgreSQL (pg8000 驱动，不装 psycopg) / S3 对象存储 / alembic 0018
**关联**: 前置规格 `.conductor/contracts/2026-11-22/f76a7264-multitask-agent-m08.contract.md`（引入多 agent，本规格方向相反）/ 关联代码 `services/orchestrator/{worker,dispatcher}.py`、`tasks/{roles,router,coordinator,states,ports,pg_stores,model_ports}.py`
**变更类型**: 重构依据（回退）

## §1 设计意图

- **一句话意图**：把被硬塞进固定功能产品的多 agent 编排去掉，让每个子任务回归单 agent loop，同时不损失多任务并发与汇总能力。
- **设计原则**：①改的是接线不改能力 ②多任务与单 agent 是正交的两层 ③废弃语义物理删净（用户明确不接受死代码/补丁/兼容层）④纯角色符号全清、任务层 turn 语义保留。
- **价值排序 / 冲突裁决**：架构纯洁性（无死代码/无兼容层）> 改动面控制 > 可回退性。用户 2026-11-23 拍板否决"暂留"方案。
- **反意图**：不加运行时开关；不留死代码/空表/废弃契约 kind 作兼容；保留 `turn`（任务层迭代语义，非角色层，不属于兼容层）。
- **与既有设计意图的关系**：改变——正式撤销 ADR-007「多 Agent 角色协作」作为默认执行层；ADR-006 的多任务扇出/汇总意图不变。

## §2 背景与目标

- **现状**：`worker.py:201-210` 构造 `_role_runner` 闭包并在 `:218` 注入 `Dispatcher`；`dispatcher.py:820-845` 在 `run()` 前插入 `Generator→Reviewer` 角色循环分支。多任务 fan-out/fan-in（planner/fanout/coordinator/task_turn）全程不依赖角色层。
- **动机**：多 agent 是为面试硬凑上去的，产品是固定功能的 widget 生成器，不需要多个自主主体；角色层增加复杂度却无真实收益。
- **影响面**：2 个接线文件 + 5 个角色层专属文件（成死代码）+ 约 15 个测试 + 契约/DB（不动）+ 文档（ADR/精读）。
- **成功标准**：每个子任务直接经 `run()` 产出，多任务 fan-out/fan-in 行为不变，测试全绿。

## §3 范围与边界

- **范围内**：worker 停止注入、dispatcher 删角色分支并改 result_ref/reason_code 来源、物理删除 roles.py/router.py 与全部角色符号、删两张 DB 表与 role 列（0018）、改事件 kind 枚举并四同步、删角色层专属测试、文档标注废弃、新增 ADR-011。
- **范围外**：多任务 fan-out/fan-in、planner/fanout/task_turn、turn 迭代语义（ADR-009）——这些是任务层，不是角色层。
- **反向排除**：不误删 coordinator.py（它是任务层 fan-in 不是 agent）、不误删 planner/fanout/task_turn、不动 GatewayPlanLLM。

## §4 技术栈

| 类别 | 选型 | 理由 |
|---|---|---|
| 编排 | asyncio + Redis Streams consumer group | 现有 dispatcher/worker 机制，不改 |
| 存储 | PostgreSQL + psycopg + alembic(head 0018) | 0018 drop 角色层两表与 role 列 |
| 测试 | pytest + `.venv/Scripts/python.exe` | 项目基线 |
| 静态检查 | ruff + pyright(`--pythonpath .venv/Scripts/python.exe`) | 项目基线 |

## §5 关键机制（接线拆解）

### 5.1 角色层是可选短路分支
`dispatcher.py:375-376` docstring 明确 `role_runner=None` 时子任务走 `run()` 内核。回退等价于"永远不注入 role_runner"。

### 5.2 必须改的两处代码
- `worker.py`：删角色层 import(176-189)、实例化(191-199)、`_role_runner` 闭包(201-210)、注入(218)；保留 GatewayPlanLLM/task_store/task_turn_store。
- `dispatcher.py`：删 820-845 角色分支；改 861-863 的 `result_ref`/`reason_code` 直接用 terminal 结果。

### 5.3 测试影响（约 15 个，全部 test_m06*）
- `test_m06_multitask_planner.py` 4 个角色 test + mock 辅助
- `test_m06_multitask_sql.py` 7 个
- `test_m06_multitask_pg.py` 4 个
- 保留所有任务层测试（规划/fanout/fan-in/GenerationTask/TaskTurn）

### 5.4 物理删净（用户拍板方案 C）
`roles.py`/`router.py` 物理删除；`GatewayGenerator`/`GatewayReviewer`/`PgAgentMessageStore`/`PgAgentWorkspace`/角色端口/角色常量全删；迁移 0018 drop 两表 + drop `generation_task.role` 列；契约 kind 21→18 并四同步；约 15 个角色层测试删除。

**关键分界（红队复审）**：
- 删 `states.py` 角色常量时必须同步删 `planner.py:24` 的 import 与 `:252` 的 `__all__` 导出，否则 NameError。
- `dispatcher.py` 删除 820-845 时 `role_ref`/`role_reason` 一并消失，同次编辑改 `:861-863` 的 `result_ref`/`reason_code` 来源，否则 NameError。
- **`turn` 不删**：`events.schema.json:50`、`generation_task.turn`、`pg_stores.allocate_turn`、`server.py:350`、`last_turn_index` 都是任务层 ADR-009 迭代语义，不是角色层。只删 `role`/`agentId`/三种 kind。

## §6 Approaches

- **A（否决）**：只改接线 + 删测试 + 文档标注，死代码/空表/契约 kind 暂留。理由：用户明确不接受死代码/补丁/兼容层。
- **B（否决）**：加 `ROLE_LAYER_ENABLED` 开关双轨。理由：保留两套分支与测试矩阵，与"改回单 agent"目标相反。
- **C（采用，用户 2026-11-23 拍板）**：物理删文件/端口/常量/测试 + 0018 迁移删表删列 + 契约 kind 21→18 四同步 + 文档 ADR-007 废弃 + 新增 ADR-011。代价是改动面大，换来无残留的干净架构。

## §10 未决假设（全部已拍板）

- U1 ✅：`RoleRunner` 协议与 `role_runner` 参数一并删除（用户：不留死参数）。
- U2 ✅：回退 ADR = **ADR-011**，ADR-007 头部加废弃声明。
- U3 ✅：物理删干净（用户否决"暂留"），加 0018 迁移。
- U4 ✅：方案 C。

## §12 验收标准

- AC-1~AC-9 见覆盖矩阵；核心：`pytest` 全绿、`contracts:check` drift=[]、0018 迁移 upgrade/downgrade 闭环、角色层零调用。

## §13 变更类型补充

本规格撤销 ADR-007 的"默认执行层"地位；ADR-006 的多任务设计不变；需在 docs/modules/00 的 ADR 索引同步撤销留痕。
