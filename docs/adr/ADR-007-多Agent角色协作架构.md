# ADR-007：多 Agent 角色协作架构（v1.0，代码已落地；§1.7 非目标已撤销）

**状态**: **v1.0 已落地（2026-11-22）**——角色层（Router 六种类型化消息、`agent_workspace` 乐观锁、Generator/Reviewer 功能角色循环 `MAX_REVIEW_ROUNDS=2` 同 hash 升级人工、模型端口 GatewayPlanLLM/GatewayGenerator/GatewayReviewer）均已改码；事件带 role/turn/agentId，AgentEventKind 17→21。**批准生效**：正式撤销 §1.7"不做多 Agent 角色编排"（留痕不删历史），D-5"不用 Agent 框架"保留。全量 **622 passed**（真 PG/Redis/Docker 集成真跑）、ruff 全绿、pyright 0 errors、contracts drift=[]。**缺口**：无真实模型端点 → 生产 fail-fast、测试 mock（§1 第 2 点按用户拍板处理）；详见 §10/§12。
**范围**: 多 widget 任务（N>1）**默认接入**的功能角色协作层（生成、评审、返修）；定义角色、通信、共享状态、预算、终止与可观测协议。单任务 N=1 仍走 ADR-006 零回归分支，不启用角色层。
**关联**: ADR-003（能力渐进披露）、ADR-004（意图去类型化）、ADR-005（进护栏）、**ADR-006（并发扇出，本 ADR 的前置任务层）**；统一阅读入口 `docs/多Widget任务的多Agent协作架构-整合设计.md`；`docs/modules/00-总览与模块地图.md` §1.6 D-5 / §1.7
**变更类型**: 架构级设计变更；用户 2026-11-22 明确"多 widget 任务下的多 agent 架构直接接进来"，本 ADR 不再是可选升级路径。批准即正式撤销 §1.7"不做多 Agent 角色编排"（留痕不删历史）。D-5"不用 Agent 框架"保留（用户已拍板手写最小协议）。
**一句话结论**: 与 ADR-006 的任务层合并后，**多 widget 任务整体就是多 Agent 架构**；本 ADR 定义其中的角色层：功能角色 + 类型化消息 + 中心化路由 + 版本化共享黑板，agent 之间只通过受控消息协作，不自由对话，不共享可写内存。

---

## §1 前置门槛（不满足不得开工）

1. ADR-006 的 P0（契约+迁移）、P1（规划器+单任务零回归）、P2（fan-out/fan-in、seq 原子化、事件 taskId）已验收——✅ **v1.0 已满足**（head=0017、kind=21、`tasks/` 任务层、seq 原子域；见 ADR-006 §12）。
2. M20 真实模型端点/分档/L4 Gateway 已接线——**本轮用户拍板处理：当前无真实模型端点/key，按"生产 fail-fast + 测试 mock"覆盖**（不静默降级、不冒充已接真模型）。`GatewayPlanLLM`/`GatewayGenerator`/`GatewayReviewer` 走真实 `deps.model`，缺 env 直接起不来（fail-fast）；协议与角色循环单测用 mock 端口；多任务/角色层端到端跑不了真模型，登记"环境阻断"（见 M07 模型缺口文档 §2.10–2.12）。**原门槛"真端点已接线"未满足，但按用户拍板以 fail-fast+mock 覆盖，不阻塞代码落地**。
3. 沙箱-runner 并发压测通过，且 cost per completed task 有基线数据（ADR-006 扇出版）——🟡 M08 真 Docker 一次性沙箱已落地（5 个集成用例 + 57 单测），但**并发压测与 A/B `cost per completed task` 基线未做**（§9 启用门槛保留）。
4. 用户拍板 O1–O5（§11）——✅ O1/O2/O3 按推荐项已收敛；O4（启动时机）随本轮 fail-fast+mock 方案落地；O5（§1.7 非目标）本 ADR 批准即撤销，已生效。

## §2 与 ADR-006 的本质差异

| 维度 | ADR-006 并发扇出 | ADR-007 多 Agent 协作 |
|---|---|---|
| 执行体 | 同一个 `run()` 的 N 个实例 | 多个**角色化** `run()`：不同 system prompt、能力白名单、目标函数 |
| 通信 | 无，彼此不知道对方存在 | 类型化消息，经中心 Router 路由 |
| 共享状态 | 无，只有各自 checkpoint | 会话级版本化黑板（PG 表 + 乐观锁） |
| 调度 | 入队 + 等终态 + 汇总 | 按消息驱动轮次，可返修、重试、升级人工 |
| 终止 | 全部子任务终态 | 协议终止 / 轮次上限 / 死锁升级 |
| 必要性 | 一次请求多个独立交付物 | **多 widget 任务（N>1）默认启用**（用户 2026-11-22 拍板） |

**接入方式**：用户拍板"多 widget 任务下的多 agent 架构直接接进来"——本 ADR 不再是"产物需要评审才可选升级"的分支，而是多 widget 任务链路的**默认执行层**。ADR-006 的任务扇出与本 ADR 的角色协作是同一条链路的两层，统一阅读入口见 `docs/多Widget任务的多Agent协作架构-整合设计.md`。

## §3 角色模型（推荐功能角色，不按 widget_type 分家）

- **Planner**：复用 ADR-006 的约束式规划器，只产出 `TaskPlan`，不生成代码、不裁决终态。
- **Generator**：按任务生成 spec / code / bundle；持有能力白名单与 retrieval 注入；无权裁决终态。
- **Reviewer**：对产物做语义、规格、安全评审，只产出结构化 `REVIEW_RESULT`（pass / fail + reason_code + evidenceRef + artifact_ref）；**无权直接改代码、无权执行沙箱**。
- **Router / Coordinator**：**不是 agent**，零模型确定性状态机；负责消息校验、目标解析、预算扣减、轮次推进、fan-in 与终态。

**明确不做"天气 agent / 日历 agent"这类按 widget 类型切分的角色**：ADR-004 已把 `widget_type` 降为 hint（未知落 `custom`），按类型建角色等于把去类型化退回去。领域知识通过能力白名单 + retrieval 按需注入，不通过角色分化。

## §4 通信协议：hub-and-spoke，不做点对点自由对话

- 所有 agent 消息**必经 Router**，不直接互发。Router 做：schema 校验 → 确定性注入/PII/敏感词检查 → 目标角色解析 → 预算扣减 → 落库 → 投递。
- 消息类型**有界**（v1 六种）：`TASK_ASSIGN` / `REVIEW_REQUEST` / `REVIEW_RESULT` / `REVISION_REQUEST` / `ESCALATE_HUMAN` / `FINAL`。
- **禁止自由文本指令**；payload 只允许「引用 + 枚举 + 结论式短句（长度上限）」，例如 `REVIEW_RESULT` 只能带 `reason_code`、`evidenceRef`、`artifact_ref`、`score`。这样跨 agent 消息就不会成为提示注入通道。
- 每条消息带：`session_id / task_id / agent_id / from_role / to_role / turn / rev / trace_id / seq`；落 PG `agent_message` 表，按 audit ledger 同样的 hash 链留证。

## §5 共享黑板（唯一共享面）

- 新表 `agent_workspace(session_id, key, value_ref, rev, writer_role, updated_at)`；写时 `UPDATE ... WHERE rev = ?`，乐观并发失败则重读重试，最多 2 次。
- 命名空间隔离：Generator 只能写 `generator:{task_id}:*`，Reviewer 只能写 `review:{task_id}:*`；读取需 Router 授权，写操作全部留审计。
- 不共享可写内存、不共享向量库写入权；黑板里的值一律是**对象存储引用 + 哈希**，不是大对象本体。

## §6 身份、状态与契约影响

| 项 | 设计 |
|---|---|
| 新标识 | `agent_id`（一次角色实例的 uuid）、`role`、`turn`（会话内轮次） |
| checkpoint | 主键仍 `(run_id, rev)`；state blob 内增加 `role/turn/agent_id`，不扩表主键 |
| 事件字段 | 在 ADR-006 的 `taskId/taskIndex` 之上再加 `role`、`turn`、`agentId` |
| 事件 kind | ✅ 新增 `agent_message` / `review` / `delegate` 三种 + ADR-008 的 `reasoning` 一种，**最终 17 → 21**（`enums.source.json` 实测 21）；已同步 schema、四生成物与 `test_m03_contract` 计数断言（21），drift=[] |
| 会话状态 | `SessionState` 仍 8 值；新增内部 `AgentSessionState`（`PLANNING / EXECUTING / REVIEWING / REVISING / ESCALATED`）仅存黑板，不进 M02 契约 |
| DB 迁移 | ✅ 实际落地为 `0016_agent_collab`（设计期编号 `0013_agent_collaboration`）：`agent_message` / `agent_workspace` 两表 + `generation_task` 加 `role/turn` 列；真 PG upgrade 通过 |
| 模型端口 | ✅ `tasks/model_ports.py` 的 GatewayPlanLLM/GatewayGenerator/GatewayReviewer 走真实 `deps.model`；生产缺端点 fail-fast，测试 mock |

## §7 预算、终止与异常

- 三级预算：session 总额 → task 分配 → turn 单轮上限；新增 `MAX_AGENT_TURNS=6`、`MAX_REVIEW_ROUNDS=2`、`MAX_DELEGATE_DEPTH=1`（均为常量，进 `enums.source.json` 的 `$constants`）。
- 正常终止：Reviewer pass 且 Coordinator 汇总全部 task → 会话 `COMPLETED`。
- 异常终止：轮次耗尽 → `RETRY_EXHAUSTED`；`ESCALATE_HUMAN` → `HUMAN_REQUIRED`；预算触顶 → `BUDGET_EXHAUSTED`；全部沿用现有 17 个 ReasonCode，**不扩码**。
- 循环检测：同一 `(task_id, role, artifact_hash)` 出现 ≥2 次即强制升级人工，禁止无限互评。
- 失败隔离：单个 task 的评审循环失败不拖垮其他 task，fan-in 仍按 ADR-006 的 `COMPLETED + notice` 规则。

## §8 安全边界

- **跨 agent 消息是不可信输入**：Reviewer 的反馈对 Generator 而言等同于用户输入，必须先过确定性检查与 schema 收窄，再进 prompt；反馈不能改 system prompt、能力白名单或护栏配置。
- 角色最小权限：Reviewer 无沙箱执行权与终态裁决权；Generator 无会话终态权；Router 无模型调用权（延续 ADR-006 O1）。
- 每个 agent 的生成产物仍过 ADR-006 的 V3 出护栏；消息留证只写引用与类别，不写敏感词原文（沿用 ADR-005）。

## §9 复现、观测与启用门槛

- `Trace` 记录全部消息、黑板 rev、角色 prompt 版本、模型 id+version、预算扣减；给定 trace 可重放一轮协作。
- M11 新增指标：每 task 轮次数、评审往返数、消息量、升级人工率、**cost per completed task**。
- **启用门槛**：在相同任务集上做 A/B 对照——`cost per completed task` 必须优于 ADR-006 单角色扇出基线，且首次通过率 / 附件规格错配率有显著改善，否则不默认启用角色层，结论写回 O 段（延续 2026-11-22 早前评估的结论）。收益论证与代价表见 `docs/多Widget任务的多Agent协作架构-整合设计.md` §1.1。

## §10 落地阶段与状态（每阶段独立验收）

**状态更新于 2026-11-22（v1.0）**：S1/S2 代码已落地并有用例（全量 622 passed）；S0 的 M20 真端点按用户拍板以 fail-fast+mock 覆盖、沙箱并发压测未做；S3 仍属未授权范围。

| 阶段 | 内容 | 出口 | 状态（v1.0） |
|---|---|---|---|
| S0 | ADR-006 P0–P2 + M20 端点 + 沙箱并发压测 | 前置门槛全部满足，有 ADR-006 基线数字 | 🟡 ADR-006 P0–P2 ✅；M20 真端点 ❌（按 §1 第 2 点 fail-fast+mock 覆盖）；沙箱并发压测 / `cost per completed task` 基线 ❌ 未做（§9 启用门槛保留） |
| S1 | 单任务双角色（Generator + Reviewer）协议闭环，mock 模型跑单测、真端点跑集成 | 消息协议、黑板乐观锁、轮次/循环升级均有用例 | ✅ 代码落地：`tasks/router.py`（六种类型化消息）、`tasks/pg_stores.py`（`agent_workspace` 乐观锁）、`tasks/roles.py`（`MAX_REVIEW_ROUNDS=2`、同 hash 升级人工）。mock 单测通过；真端点集成 ❌（无端点） |
| S2 | 多任务 × 角色：每 task 独立角色循环，Router fan-in | 3 task 并发协作，事件无撞号，成本按 task+turn 分账 | ✅ 代码落地（task 层单测 12 + DB 层 24 + `test_m06_multitask_pg` 真 PG）；事件带 `role/turn/agentId`、seq 原子域无撞号；真模型端到端联调 ❌（无端点，环境阻断） |
| S3 | 可选：动态委托 / 领域专精角色 | 需再次评审，不自动视为本 ADR 授权范围 | ❌ 未做（S3 不在本轮授权范围，需另起评审） |

## §11 未决（需用户拍板）

| # | 未决项 | 推荐 | 影响 |
|---|---|---|---|
| O1 | 角色按什么维度切 | **功能角色**（Planner/Generator/Reviewer）；按 widget 类型切会与 ADR-004 去类型化冲突 | 按类型切需先撤销 ADR-004 的部分结论 |
| O2 | 是否仍坚持 D-5"不用 Agent 框架" | **手写最小协议**（消息/黑板/路由），不引入 LangGraph 等 | 引入框架要重评非目标、依赖、部署与契约门禁 |
| O3 | 通信拓扑 | **中心路由 + 类型化消息** | 点对点自由通信会放大提示注入与不可控循环 |
| O4 | 启动时机 | **先完成文档整合（已做）**；代码待 ADR-006 P0–P2 + M20 端点，现在只写 mock 协议单测 | 提前上生产会因无真实模型而 fail-fast |
| O5 | §1.7 非目标 | 本 ADR 批准即**正式撤销**该条，留痕不删历史 | 不撤销则本 ADR 与全局基线冲突 |

## §12 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 草案 | 2026-11-22 | 首次提出；定义功能角色、中心路由类型化消息、版本化黑板、三级预算与终止协议；明确前置依赖 ADR-006 P0–P2 + M20，正式撤销 §1.7 待批准 |
| v1.0 已落地 | 2026-11-22 | 角色层代码全部落地并验收（全量 622 passed）：Router 六种类型化消息 + `agent_workspace` 乐观锁 + Generator/Reviewer 角色循环（MAX_REVIEW_ROUNDS=2，同 hash 升级人工）+ GatewayPlanLLM/GatewayGenerator/GatewayReviewer 真实模型端口 + 事件 role/turn/agentId；kind 17→21、迁移 `0016_agent_collab`。§1 第 2 点按用户拍板以 fail-fast+mock 覆盖"M20 真端点"门槛；§1.7 非目标正式撤销生效；登记模型端点缺失与沙箱并发压测/AB 基线未做两项缺口。 |
