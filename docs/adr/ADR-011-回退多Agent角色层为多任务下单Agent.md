# ADR-011：回退多 Agent 角色层为多任务下单 Agent

**状态**: **v1.0 已落地（2026-11-23）**——角色层（ADR-007）已物理删除，每个子任务直接走单 agent `run()` 内核；多任务 fan-out/fan-in（ADR-006）完整保留。全量 **661 passed**（真 PG/Redis/Docker 集成真跑）、ruff 全绿、pyright 0 errors、contracts drift=[]、alembic head=0018（upgrade/downgrade 双向验证）。
**范围**: 一次请求多 widget 的执行层。任务拆分与会话汇总不变，只去掉每个子任务内的多角色协作。
**关联**: 取代 **ADR-007（已废弃，见其头部声明）**；沿用 ADR-006（任务扇出）、ADR-009（多轮迭代）；与 ADR-010（设备 API，编号无关）不冲突。
**变更类型**: 架构回退；用户 2026-11-23 明确"不接受死代码、补丁、兼容层"，故物理删净而非加开关。

---

## §1 背景与动机

ADR-007 于 2026-11-22 引入多 Agent 角色协作（Router 六种消息 + `agent_workspace` 黑板 + Generator/Reviewer 循环），当时定位为"多 widget 任务默认执行层"。经评估确认：本项目是**固定功能的 widget 生成产品**——输入固定（描述 + 附件）、输出固定（手机控件）、约束固定（沙箱/能力白名单）。它不存在多个真实的自主主体，也不存在必须靠目标冲突协商才能解决的任务。角色层是为"架构好看"硬加的，增加状态一致性与冲突收敛复杂度却无收益。

用户决策：把"多任务下的多 agent"回退为"多任务下的单 agent"。

## §2 设计意图

- **一句话**：改的是接线不是能力——把 `run()` 前的可选角色分支去掉，让子任务直接进单 agent loop。
- **原则**：①多任务与单 agent 正交 ②废弃语义物理删净（无死代码/空表/兼容 kind）③`turn` 是任务层语义必须留。
- **反意图**：不加 `ROLE_LAYER_ENABLED` 开关（保留两套分支与测试矩阵，与目标相反）；不留兼容层。

## §3 具体变更

| 层 | 删除 | 保留 |
|---|---|---|
| 接线 | `worker.py` 的 `_role_runner` 注入；`dispatcher.py` 的角色分支与 `role_ref`/`role_reason` | `run()` 调用链、fan-in、planner/fanout |
| 端口 | `RoleRunner`/`GeneratorPort`/`ReviewerPort`/`AgentMessage`/`ReviewResult` | `PlanLLM`/`EventEmitter`/`TaskStorePort`/`TaskTurnStorePort` |
| 常量 | `states.py` 的 `Role`/`MessageType`/`AgentSessionState`/`MAX_AGENT_TURNS` 等 | `TaskState`/`terminal_reason_code` |
| 存储 | `PgAgentMessageStore`/`PgAgentWorkspace` 及其 ORM；`roles.py`/`router.py` 文件 | `PgGenerationTaskStore`/`PgTaskTurnStore` |
| DB | 0018 drop `agent_message`/`agent_workspace` 表 + `generation_task.role` 列 | `generation_task.turn` 列（ADR-009 迭代语义） |
| 契约 | `AgentEventKind` 21→18（删 agent_message/review/delegate）；删 `role`/`agentId` 字段 | `turn` 字段、`taskId`/`taskIndex` |
| 测试 | 约 15 个角色层测试 | 全部任务层测试（规划/fanout/fan-in/SQL/PG） |

## §4 关键坑（落地记录）

1. `states.py` 删角色常量时必须同步删 `planner.py` 的 import 与 `__all__` 导出，否则 import 期 NameError。
2. `dispatcher.py` 删角色分支时 `role_ref`/`role_reason` 局部变量一并消失，必须在同次编辑改 `result_ref`/`reason_code` 来源，否则 NameError。
3. `TaskStorePort.update_state` / `EventEmitter.emit` 协议里的 `role`/`agent_id` 参数要与实现同步删，否则 pyright 报协议不兼容。
4. `generation_task.role` 列删除后，`GenerationTaskData` 数据类与 pg_stores 的 create/update/summary 里所有 `role` 引用必须同步删，否则 SQLAlchemy `Unconsumed column names: role`。
5. 0018 的 `downgrade` 需重建两表（列定义对齐 0016：payload 用 `JSONB`、`pk_agent_workspace` 显式主键名）与 role 列，已验证 upgrade→downgrade→upgrade 闭环，且 downgrade 后用 SQLAlchemy 查 information_schema 确认 payload=jsonb、主键名一致、role 列恢复。**但 drop 表数据不可恢复**：downgrade 只重建空结构，角色层历史留证数据永久丢失；rollback 需连代码一起回退（角色层代码已删，仅恢复表无意义）。

## §5 影响与兼容

- **事件 kind 21→18**：schema 容忍未知 kind（前端 reducer 忽略），老事件不受影响；四生成物已同步，`contracts:check` drift=[]。
- **DB**：0018 是破坏性迁移（drop 表/列），downgrade 可恢复结构但不恢复历史消息数据（角色层留证本就无业务消费者）。
- **ADR-007**：整篇标注"已废弃 / 被 ADR-011 取代"，历史留痕不删。

## §6 验收

- 全量 `pytest -q` → 661 passed（含真 PG 集成）
- `alembic upgrade head` → 0018；`downgrade 0017` → 再 upgrade 闭环通过
- `ruff check .` / `pyright` / `pnpm contracts:check` → 全绿 / 0 errors / drift=[]
- 新增 `test_single_agent_path.py`：断言 `Dispatcher` 无 `role_runner` 参数、角色符号生产代码零命中、任务层符号仍在

## §7 未决 / 后续

- 无已知功能缺口。角色层的历史消息表数据随 0018 drop 丢弃（无消费者）。
