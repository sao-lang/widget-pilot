# 多 Widget 任务的多 Agent 协作架构：整合设计

**状态**: 草案整合视图（2026-11-22）；**多 widget 任务直接采用多 Agent 角色协作**，不是"扇出 vs 多 agent"二选一
**范围**: 一次用户消息要求生成 ≥2 个 widget 时，从入口校验、约束式规划、任务扇出，到每个任务内 Generator/Reviewer 角色协作、汇总回传的完整链路
**关系**: `docs/adr/ADR-006-一次对话多控件生成的主控调度与子任务并发.md`（任务层：身份、事件、扇出、校验时序）、`docs/adr/ADR-007-多Agent角色协作架构.md`（角色层：角色、消息、黑板、终止协议）。本文是两者的统一阅读入口；单项决策与变更记录仍留在 ADR。
**当前进度**: **文档阶段**。用户已拍板 ADR-006 的 O1/O3/O5 与规划器方向，ADR-007 的 O1/O2/O3 按推荐项收敛，并明确"多 widget 任务下的多 agent 架构直接接进来、先写文档"。未改生产代码，未跑测试。

---

## §0 一句话结论

多 widget 请求 = **一次约束式规划 → N 个独立任务扇出 → 每个任务直接跑一轮多 Agent 角色协作（Generator → Reviewer → 必要时返修）→ 确定性 Coordinator 汇总单条 `done`**。
任务层的扇出（ADR-006）与角色层的协作（ADR-007）是**同一条链路的两层**，不是两个可选方案。单任务（N=1）仍走现有固定流程，零回归。

**N 的定义**：N = 一次用户消息经约束式 Planner 切出的**独立 widget 任务数**，即 `TaskPlan.tasks.length`。它**不是** agent 数量、不是并发度、也不是评审轮数。

- N 由规划器决定，但只能引用 `PlanningFacts` 中的附件/规格，且必须经 V2 校验；用户不直接填 N。规划器给出的 N 不是最终事实——V2 发现规格或附件歧义会打回重规划，**以通过 V2 的 TaskPlan 为准**。
- `N=1`：现有固定流程，零回归，不启用角色层。
- `N>1`：扇出 N 个 `generation_task`，每个任务一个稳定 `task_id` 与独立 `run_id`；每个任务内部固定跑 Generator / Reviewer 两个功能角色（最多 2 轮返修）。**角色数与 N 无关**——N=3 时是 3 个任务 × 2 个角色实例，不是"3 个 agent"。
- N 受上限约束：`WIDGETPILOT_MAX_PARALLEL_TASKS` 默认 3、硬上限 8；超过则 V2 询问或拒绝，不会静默拆更多。**并发度 ≤ N**：N=5 且上限 3 时只有 3 个任务同时在跑，N 仍是 5。
- 示例："做一个时钟" → N=1；"做一个时钟和一个 4x4 日历" → N=2；"天气、日历、倒计时三个，附件 A 给日历" → N=3。

## §1 为什么多 widget 任务要直接接多 Agent

- 多 widget 请求的错误会**按任务数放大**：附件错配、规格漂移、派生描述夹带注入，单任务时偶发，N 个任务时必然出现。
- 原 ADR-006 的扇出版只有"生成后出护栏"，属于**事后拦截**；Reviewer 角色是在产物落地前做语义/规格/附件一致性评审，能把返修留在会话内，而不是让用户看到坏产物再重试。
- 用户拍板：角色按**功能维度**切（不按 widget 类型，避免回退 ADR-004 的去类型化），继续手写最小协议（D-5 不变），通信走中心路由 + 类型化消息。

### 1.1 相对单 Agent 扇出的收益与代价（待真实验证）

**基线 A（单 agent）**：现有 `run()` 原样并发扇出，即 ADR-006 的 P0–P2，无角色层。
**决策 B（多 agent）**：A + 每个任务默认跑 Generator / Reviewer 角色循环与局部返修。

| 维度 | A 单 agent 扇出 | B 多 agent 角色协作 | 收益成立条件 |
|---|---|---|---|
| 产物验收 | 生成后靠 V3 出护栏；坏产物落地才拦截，需整任务重试 | Reviewer 在产物落地前做语义/规格/附件一致性评审，fail 则局部返修 | 规格越复杂、附件越多，收益越大 |
| 错误放大 | 单任务出错概率 p，N 任务至少一处出错为 `1-(1-p)^N`，随 N 上升 | 每任务独立验收，返修只动失败任务，不重跑规划与其他任务 | **仅 N>1 成立**；N=1 启用角色层纯亏，故保留零回归分支 |
| 自评偏差 | 同一上下文既生成又自查，容易沿用自身假设 | Reviewer 独立 prompt、只看产物与证据，不带生成时的推理路径 | 对"看起来合理但附件错配"类问题尤其有效 |
| 权限爆炸半径 | 单 loop 同时持有生成、沙箱、终态权 | 最小权限：Reviewer 无沙箱/终态权，Generator 无终态权 | 注入或误判时更难直接落坏产物 |
| 可观测与归因 | 只有 run 级事件，多任务并发时难定位哪个 widget 哪步坏 | `taskId / role / turn / agentId` + 黑板 rev + Trace 可重放 | 并发任务越多，收益越明显 |
| 延迟与成本 | 一轮生成 | 多一轮评审 + 最多 2 轮返修 | **代价**：p95 延迟与 token 上升，必须用数据验证 |

**所以收益不是"绝对更好"，而是"随任务数与规格复杂度增长"**。必须用 A/B 对照拿数据说话：

- 质量指标：首次通过率、用户重试率、附件/规格错配率
- 效率指标：`cost per completed task`、p95 延迟
- 稳定性：返修轮次分布、循环升级率、事件归因成功率

**门禁**：P3 完成后在相同任务集上做 A/B；若 `cost per completed task` 不优于 A，或首次通过率无显著提升，则不默认启用角色层，回退到单 agent 扇出并把结论写回 ADR-007 的 Open 段。当前无真实模型端点，以上收益均为**待验证假设**，不是已实现事实。

## §2 完整链路（编号，两层合一）

1. **V0 入口（不变）**：Pydantic `extra="forbid"` → JWT/TenantGuard → 附件一次上传为 `attachment_ids`。
2. **建父 run（不变）**：`submit_message` 分配 `parent_run_id`（复用 `Session.run_id`），保存 `original_description`。
3. **入队规划任务一条**：带 `planning=1`；"1 次 post = 恰好 1 条"断言保持成立。
4. **V1 前置安全门禁（规划前）**：越界、`check_input`（注入/PII/敏感词）、"是不是控件生成请求"意图白名单；**不做单 widget 分类、不做逐任务规格判定**，否则多意图消息整条误拒。
5. **约束式规划（Planner，模型驱动）**：`FactExtractor` 零模型产出 `PlanningFacts`（原文证据、附件 id/role/摘要、grid、预算）；`PlanLLM` 输出严格 JSON `TaskPlanDraft`；`PlanValidator` 零模型 fail-closed + 有界修复 1 次；落 `PlanTrace`。
6. **V2 计划级校验（fan-out 前）**：附件引用真实性/role 兼容、派生描述过 `check_input`、逐任务规格、任务数/预算、依赖检测；歧义 → `AWAITING_INPUT`，不猜。
7. **落库并分流**：
   - **N=1**：worker 把规划任务 ACK 后，按原有 `TaskFields` 入队 **1 条普通任务**（`planning=0`），由既有的 `process_one → run()` 消费。执行的是现有手写 agent loop：`decide()` 九分支（CALL_MODEL / LOAD_CAPABILITY / REPAIR / ASK_USER / RESUME / AWAIT_INPUT / 终态等）→ 模型调用 → checkpoint 落盘 → 发事件 → `meter.check` → 续租；**原有的 REPAIR 分支 + V3 出护栏已经承担"生成后自检 + 返修"**，所以不需要再套一层独立 Reviewer。事件仍发原来的字符串 `plan`，不带 `taskId` / `role`，终态回写与 SSE 行为与今天一致，零回归。
   - **N>1**：写 N 行 `generation_task`，`task_id = uuid5(parent_run_id, index)`；每个任务进入角色协作循环。

**所以 N=1 不是"不执行"或"不校验"**：V1 安全门禁、V2 计划校验、现有 REPAIR 自修、V3 出护栏全都保留；只是不引入独立评审角色、类型化消息和共享黑板，避免纯开销。
8. **任务内多 Agent 协作（N>1 默认启用）**：
   - **Generator**：按本任务的派生描述 + 附件子集生成 spec/code，写黑板 `generator:{task_id}:spec`。
   - **Router**（零模型）：把 `REVIEW_REQUEST` 做 schema/注入检查后投递给 Reviewer，扣减 turn 预算并记录 trace。
   - **Reviewer**：读黑板产物，产出结构化 `REVIEW_RESULT`（pass/fail + reason_code + evidenceRef + artifact_ref），写 `review:{task_id}:*`。
   - **返修**：fail 时 Router 发 `REVISION_REQUEST` 回 Generator；`MAX_REVIEW_ROUNDS=2`，重复同一产物哈希即强制升级人工。
   - **V3 出护栏**：每轮生成产物仍走现有语义校验/沙箱门禁，Reviewer 不替代它。
9. **Coordinator 汇总（fan-in）**：轮询 + 事件驱动检查任务与评审状态；全部通过后发唯一会话级 `done`，payload 带每个 task 的 bundle / reason_code。
10. **终态**：全部成功 → `COMPLETED`；部分成功 → `COMPLETED + notice`（失败 task_id + 原因码）；全失败 → `FAILED`；循环耗尽或升级 → 对应 task `HUMAN_REQUIRED` / `RETRY_EXHAUSTED` 并计入 `notice`。

## §3 角色与权限（功能角色，中心辐射）

| 角色 | 是否 agent | 目标 | 权限边界 |
|---|---|---|---|
| Planner | 是（模型驱动） | 产出受事实约束的 TaskPlan | 不生成代码、不入队、不裁决终态 |
| Generator | 是（模型驱动） | 按任务生成 spec/code | 无终态权；只能写 `generator:{task_id}:*` |
| Reviewer | 是（模型驱动） | 产出结构化评审结论 | 无沙箱执行权、不改代码、无终态权；只写 `review:{task_id}:*` |
| Router / Coordinator | **不是 agent** | 消息校验/路由/预算；任务 fan-in | 零模型确定性状态机，无模型调用权 |

- **不做点对点自由通信**：所有消息经 Router，payload 只允许「引用 + 枚举 + 结论式短句」，禁自由文本指令，防提示注入与不可控循环。
- **不做按 widget 类型的角色**（如天气 agent/日历 agent）：领域知识通过能力白名单 + retrieval 注入，不通过角色分化回退 ADR-004。
- **共享面唯一**：PG 表 `agent_workspace`，按命名空间隔离，`rev` 乐观锁；只存对象存储引用 + 哈希。

## §4 身份、事件与状态（合并两 ADR）

| 项 | 设计 |
|---|---|
| 标识 | `session_id` → `parent_run_id` → `task_id` → `run_id`；角色层再加 `agent_id` / `role` / `turn` |
| 事件字段 | 在现有 8 个 required 字段上加 `taskId` / `taskIndex` / `role` / `turn` / `agentId`（均可选） |
| 事件 kind | 17 → 20：新增 `agent_message` / `review` / `delegate`；同步 `enums.source.json`、schema、四生成物与 `test_m03_contract.py` 计数断言 |
| 事件归因 | `done`/`error` 带 `taskId` = 任务级；不带 = 会话级，只有 Coordinator 能发会话级 `done`/`error` |
| seq | Redis 原子 `INCR` 统一域 `wp:eventseq:{session_id}`，以 `max(session.last_seq, 窗口最大 seq)` 做 seed；解决并发撞号 |
| SessionState | 仍 8 值，不扩；任务/角色状态放内部枚举与黑板 |
| checkpoint | 主键 `(run_id, rev)` 不变，state blob 内带 `role/turn/agent_id` |
| ReasonCode | 仍 17 个不扩：升级用 `HUMAN_REQUIRED`、返修耗尽用 `RETRY_EXHAUSTED`、超预算用 `BUDGET_EXHAUSTED` |

## §5 预算、终止与可复现

- 三级预算：session 总额 → task 分配 → turn 单轮；常量 `MAX_AGENT_TURNS=6`、`MAX_REVIEW_ROUNDS=2`、`MAX_DELEGATE_DEPTH=1`。
- 循环检测：同一 `(task_id, role, artifact_hash)` 出现 ≥2 次强制升级人工。
- 失败隔离：一个 task 的评审循环失败不影响其他 task。
- `Trace` 记录全部消息、黑板 rev、角色 prompt 版本、模型 id+version、预算扣减与 `PlanTrace`；给定 trace 可重放一次多 widget 请求。
- 沙箱与沙箱-runner 并发能力需在 P2 前压测；`guard_hit` advisory lock 在 N 大时可能成为热点。

## §6 契约与数据变更总表

| 类别 | 文件 / 迁移 | 变更 |
|---|---|---|
| 事件 schema | `packages/contracts/events.schema.json` | 加 5 个可选字段；新增 3 个 kind；`plan` oneOf 增对象分支；error/notice discriminator 保持 fail-closed |
| 枚举 | `packages/contracts/enums.source.json` | `AgentEventKind` 17→20；`$constants` 加 3 个上限；SessionState/ReasonCode 不变 |
| REST | `packages/contracts/openapi.yaml` | clarifications 加可选 `taskId`；`GET /sessions/{id}` 加 `tasks[]` 摘要 |
| Python 契约 | `agent_core/types.py:AgentEvent`、`contracts/types.py:TaskFields` | 手写镜像同步 |
| 生成物 | `pnpm gen:enums/zod/openapi/python` | 四同步，`pnpm contracts:check` drift=[] |
| DB | `0012_generation_tasks`、`0013_agent_collaboration` | 任务表 + `agent_message` / `agent_workspace`；`guard_hit`/audit 加 task_id |
| 模型端口 | `PlannerPort` / `GeneratorPort` / `ReviewerPort` | 真实接线 + 生产 fail-fast；测试 mock；登记模型缺口文档 |
| 测试 | `tests/unit/test_m03_contract.py` 等 | kind 计数 20；单任务入队/终态断言不变；新增并发、消息、黑板与返修用例 |

## §7 已拍板 / 假设 / 未决

| 状态 | 项 | 结论 |
|---|---|---|
| 已拍板 | 主控形态 | 确定性调度器；调用模型的是规划器与各角色 agent，不是主控 |
| 已拍板 | 部分成功 | `COMPLETED + notice`，不新增 `SessionState.PARTIAL` |
| 已拍板 | 计划确认 | 仅歧义时确认；歧义重跑规划而非补字段 |
| 已拍板 | 规划器 | 事实抽取 + 约束式 LLM + 零模型校验，否决纯规则 |
| 已拍板 | 角色维度 | 功能角色 Planner/Generator/Reviewer，不按 widget 类型 |
| 已拍板 | 框架 | 继续手写最小协议，不引入 Agent 框架（D-5 不变） |
| 已拍板 | 通信 | 中心路由 + 类型化消息，不做点对点自由通信 |
| 已拍板 | 本次范围 | 先写文档整合；多 widget 任务直接接多 Agent 架构，不改码 |
| 假设 | 并发上限 | 默认 3、硬上限 8（O4 未正式拍板，按此推进） |
| 未决 | §1.7 非目标 | ADR-007 批准即正式撤销"不做多 Agent 角色编排"，留痕不删历史 |

## §8 路线图（每阶段独立验收）

| 阶段 | 内容 | 出口 |
|---|---|---|
| 文档（当前） | 本文 + ADR-006 v0.2 + ADR-007 v0.1 + 索引同步 | 三份文档口径一致，无相互矛盾的分支描述 |
| P0 | 契约 + `0012`/`0013` 迁移 + 四生成物同步 | `contracts:check` drift=[]；alembic head=0013；现有测试不降 |
| P1 | `FactExtractor` + `PlanLLM`（mock） + `PlanValidator`；N=1 零回归 | 单任务全绿；规划器单测覆盖幻觉附件 id、歧义、有界修复 |
| P2 | 任务扇出/汇总、独立 run、seq 原子化、事件 taskId | 3 任务并发，seq 无重复无缺口，成本 3 行独立 |
| P3 | **角色层直接接入多 widget 任务**：Router、黑板、Generator/Reviewer、返修与循环升级 | 3 任务各跑一轮评审；事件带 role/turn/agentId；单条 `done` |
| P4 | 澄清 / 取消 / 预算 / 文档与模型缺口同步；真实端点联调 | 全部验收用例通过；无真实端点时多 widget 登记环境阻断 |

## §9 验收要点（合并）

1. 单任务：入队 1 条、事件无 `taskId`、不启用角色层、终态与现状一致。
2. 多 widget：规划出 N 任务，每任务直接产生 Generator 与 Reviewer 的 `agent_message` 事件，返修轮次 ≤2。
3. 并发：N 个独立 `run_id`、N 行 cost_ledger、SSE seq 严格递增；带 `taskId/role` 可分区。
4. 幻觉附件 id 或派生描述注入 → V2 拒绝，不落 `generation_task`，不进角色层。
5. 评审 fail → 返修；同一产物哈希重复 → `HUMAN_REQUIRED`，会话不提前 `FAILED`。
6. 全部通过 → 单条 `done` + `COMPLETED`；部分失败 → `COMPLETED + notice`。
7. V1 命中注入 → 模型端口（Planner/Generator/Reviewer）调用次数均为 0。
8. 父级 cancel → 所有未终态任务与角色循环在下一个边界转 `CANCELLED`。

## §10 与 ADR 的关系

- **ADR-006** 保留为任务层决策记录；其 §2.1 的"本方案不是多 agent"指的是**单独看扇出层**。与 ADR-007 合并后，多 widget 任务整体就是多 Agent 架构。
- **ADR-007** 保留为角色层决策记录；其"仅当产物需要评审才上"的选择分支，按本次用户拍板改为**多 widget 任务默认接入**。
- 本文是统一阅读入口；若决策再变，先改对应 ADR，再同步本文，避免双源漂移。

## §11 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-22 | 首次整合；按用户澄清将多 Agent 角色层直接接入多 widget 任务链路，取消"扇出 vs 多 agent"二选一表述 |
