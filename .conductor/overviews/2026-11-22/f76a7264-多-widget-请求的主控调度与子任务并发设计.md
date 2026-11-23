## 追加：回退多 agent → 多任务下单 agent（S1/S2 方案，待用户确认；2026-11-23）

- 场景/路径/档位：6 设计驱动重构（用户先要方案后改码，本轮只做 S0-S2）/ code / **full**（置信 0.9）
- S1 声明：目标=把子任务内多 agent 角色协作去掉，保留多任务 fan-out/fan-in；下一站=用户确认契约 → S3 TDD 改码
- 省掉：subagent（未命中≥2独立任务信号）；git（用户"先不提交"）；ledger（无多项承诺/模块清单）；**改码本轮不做**（用户明确先出方案）
- 路由卡：场景6 full，器官链 plan→brainstorm→impact→tdd→review→memory，覆盖矩阵必建
- 核心事实（只读调研）：角色层是 dispatcher.py:820-845 的可选短路分支，role_runner=None 时子任务本就走 run()；多任务 fan-out/fan-in 不依赖角色层，可原样保留
- S2 产物：spec `.conductor/contracts/2026-11-23/f76a7264-rolelayer-rollback.spec.md` + coverage 同目录 `.coverage.md`（8 行全勾销，coverage_check PASS）+ 六段契约 `.contract.md`（contract_check PASS，Acceptance=9）
- 方案 A（采用）：worker 停注入 + dispatcher 删分支 + 删约 15 个角色层测试 + 文档标注废弃；死代码/空表/契约 kind 暂留不物理删（可回退优先）
- 红队复审修正：states.py 角色常量本轮保留（方案A不删），planner.py:24/252 不动；dispatcher 删 role_ref/role_reason 局部变量后必须同编辑改 861-863 result_ref 来源，否则 NameError
- **门禁状态（初版方案A）**：覆盖矩阵 PASS / 契约 PASS，但 Open U1-U4 非空 → 契约=draft，规格未确认 → 不进 S3（conductor 硬门禁）
- **用户拍板（2026-11-23 08:58）**：不接受死代码/补丁/兼容层 → 否决方案 A，改方案 C（物理删干净）。U1=删 RoleRunner、U2=ADR-011、U3=0018 删表删列、U4=方案 C
- 契约已改方案 C 并重跑门禁：coverage_check PASS（8 行）/ contract_check PASS（Acceptance=9）/ Open 清零 → **契约 confirmed、规格已确认，可进 S3**
- 方案 C 边界：`turn` 语义保留（任务层 ADR-009），只删 role/agentId/三种 kind；0018 drop agent_message/agent_workspace 表 + drop generation_task.role 列
- **环境阻断**：docker info 失败（daemon 未起）→ 0018 迁移与 PG 集成测试无法真实验证；pytest 单测/ruff/pyright/contracts drift 可跑。按 conductor 硬规则登记环境阻断，不砍功能、不造假绿

# 会话摘要：多 widget 请求的主控调度与子任务并发设计

## L0 索引卡（≤8 行，2026-11-22 20:5x 续写覆盖本卡）
- 场景 / 路径 / 档位：6 设计驱动重构 + 4 开发 / router→brainstorm(拷问)→change-contract→tdd→subagent→review→memory / **full**（置信 0.9）
- 声明：目标=落 ADR-006/007/009/整合设计 + M08/ADR-004 未完成功能的用户四拍板；下一站=P0 契约/迁移；环境 Docker 可用（基线 528 passed 真跑）
- 省掉：无（full 全跑）；前序未提交改动保留不回退、不纳入本轮提交
- 事实基线：M08 guardrails 四件套 + sandbox_runner 六件套全缺，executor.py 无隔离自述禁用；事件 kind 实际 17 而文档 18/19/20 冲突；迁移止 0014；bundle_ref/generation_task/task_turn 全缺
- 用户四拍板：①事件 kind 统一 21；②ADR009 三项前置全量补齐；③M08 沙箱宿主机直调 Docker 真隔离；④分阶段多次提交（精确 git add）
- 产物：契约 `.conductor/contracts/2026-11-22/f76a7264-multitask-agent-m08.contract.md`（contract_check PASS / Acceptance=5 灌台账 5 条 pending）
- 拷问 Blocker B1–B7 已并入契约 Delta/Invariants；设计改动须同步 ADR-006/007/009/整合/M08/00 总览，留痕本 overview

## 旧 L0 索引卡（历史，保留）
- 场景 / 路径 / 档位：2·3 设计 / 文档（router → brainstorm → ADR 草案 → 用户拍板）/ lite，置信 0.85
- 声明：目标=给一次对话生成 2+ widget 的可评审方案；下一站=用户确认 O4 后进 S2 规格固化 + change-contract；不改码
- 省掉：impact / subagent / tdd / verify / review_gate / ledger / git / change-contract（无代码变更且规格未确认，依据 lite 条件加载表）
- 事实基线：当前严格 1 session → 1 run_id → 1 队列任务 → 1 agent loop；无 task_id / 任务图 / fan-out；seq 由事件重算，SSE 单 session key
- 产物：`docs/adr/ADR-006-一次对话多控件生成的主控调度与子任务并发.md`（v0.2 草案：约束式 LLM 规划 + 校验三明治）+ `docs/modules/00-总览与模块地图.md` ADR 索引同步
- 已拍板：O1 确定性主控（规划器才调模型）/ O3 COMPLETED+notice / O5 仅歧义确认；O2 用户否决纯规则，改为事实抽取+LLM 约束规划+零模型计划校验（§6.7）；O4 并发上限按默认 3 硬 8 假设，待确认
- 降级：仅做只读探索 + 文档，未跑 pytest/contracts（无代码改动）；数字均引自源码/现有文档，未凭记忆编造

## 追加：ADR-007 多 Agent 角色协作（用户追问"真要拆多 agent 怎么做"）

- 场景 / 路径 / 档位：2·3 设计 / 文档（沿用本 overview 的 router→brainstorm）/ lite；置信 0.8
- 声明：目标=给真正的多 agent 角色协作给出可评审架构；下一站=O1–O5 拍板 → 规格固化；不改码
- 省掉：同 L0（无代码变更、规格未确认）；新增 ADR-007 草案 + 索引同步
- 核心：功能角色（Planner/Generator/Reviewer）+ 中心路由类型化消息 + 版本化黑板 + 三级预算/终止协议；批准即正式撤销 §1.7 非目标
- 硬门槛：ADR-006 P0–P2 + M20 真模型端点 + 沙箱并发压测；启用门槛为 cost per completed task 优于扇出基线
- 未决：O1 角色维度（功能角色推荐）/ O2 是否坚持 D-5 手写 / O3 中心路由 vs 点对点 / O4 启动时机 / O5 撤销 §1.7

## 追加：用户澄清"整合"含义 + 文档整合完成

- 用户拍板：ADR-007 O1 功能角色 / O2 手写最小协议（D-5 保留）/ O3 中心路由+类型化消息；O4 明确为"先写文档，把 ADR-006 和 ADR-007 整合"。
- 用户进一步澄清："整合"= **多 widget 任务下的多 agent 架构直接接进来**，不是两个方案二选一。已据此写 `docs/多Widget任务的多Agent协作架构-整合设计.md`：N>1 默认走任务扇出 + 每任务内 Generator/Reviewer 协作，N=1 零回归。
- 已同步 ADR-006 §2.1（合并后多 widget 整体是多 agent）、ADR-007 头部/§2 接入方式/O4、模块地图统一阅读入口。
- 仍未改码、未跑测试；代码阶段待 ADR-006 P0–P2 + M20 端点 + 沙箱压测。
- **提交（2026-11-22 03:2x）**：`30598bd docs(arch): 设计多 widget 任务的多 Agent 协作架构` — 5 文件 / +544 −2；hooks 通过，未使用 --no-verify；未 push（用户仅要求提交）。

## 追加：代码考古——去类型化后为何仍有 widget_type 字段（场景15 只读）

- 场景 / 路径 / 档位：15 代码考古 / router→review(audit-archaeology) / lite；只读，零代码改动。
- 结论：去类型化去的是「按类型判定」语义，不是字段本身。ADR-004 §1.6「契约只收窄不换形」+ FR-12 白纸黑字要求字段/事件键保留、值恒 `custom`、不参与放行/升级。
- 证据链：`validate_chain.py:346 widget=WidgetType.CUSTOM`（blame `3cb96a9` 去类型化收尾提交，注释写明"契约字段保留但值恒 custom"）；字段本身 `guardrails/types.py:249` 来自 `dd54838f`（早于去类型化）；`widget_spec.schema.json:17`/`widget_bundle.schema.json:48` enum 来自 `0a2bdbb`（11-16 初期契约）。
- 分类：①故意保留的兼容壳 = ValidationResult 字段 + routing 事件键 + 两份契约 schema enum + `WidgetType` 枚举本体（`enums.py:31-36`，其余 4 值生产代码零消费者，仅 `custom` 被赋值）；②另一语义非同-Object = M07 记忆/偏好域 `widget_type`（`memory/types.py:158`、`preference_learning.py:64`、`server.py:159 preferred_widget_types`，`blame 047c14dc` 11-22 引入，是数据归档键不是判定门禁）；③历史遗留 = `0009_drop_skill_prefetch.py` 的 widget_type 列只在已废弃表的 downgrade 重建路径。
- 存疑标注：程序记忆 `hint_text(user_id, widget_type)`（`slots.py:137` ← `dispatcher.py:450` ← `validation.widget_type`）因上游恒 `custom`，按类型匹配维度实际退化；偏好域 `preferred_widget_types` 由用户显式选择仍有区分度。非 bug，登记不修。

## 追加：ADR-009 多轮对话独立部分落地（场景链 9 拷问 → 6 改码 / full）

- 场景 / 路径 / 档位：段1 场景9 拷问（只读）→ 段2 场景6 设计驱动重构（改码）；full（多模块 + DB 迁移 + 架构变更 + 有未决清单 + 真实环境依赖）
- 声明：用户要求"未决项按推荐处理 + 拷问文档 + 没问题按 conductor 落代码"；设计闸门=拷问无 Blocker 才进改码；收紧范围后无 Blocker
- 拷问结果：发现 ADR-009 完整设计硬依赖未落码的 ADR-006（generation_task/task_id）→ Blocker；用户拍板 A 方案（只落独立部分）+ 产物复用 SessionRow.bundle_ref 覆盖；O1–O7 全部按推荐落定（写入 ADR v0.2）
- 规格固化：`.conductor/contracts/2026-11-22/f76a7264-adr009-multiturn.contract.md` 六段（contract_check PASS / Acceptance=4）→ 灌台账
- 省掉：subagent 派发（任务间共享 types.py/slots.py，并行会冲突）；env_probe 已跑（Docker 不可用）；verify.py 模板命令与项目环境不符（venv/无 src/pyright），降级为项目实测命令作证据（已在台账登记）
- 落地产物：FactSummary 零 LLM 模块 + PgFactSummaryStore + 迁移 0014_fact_summary + COMPLETED 可再提交 + 结论槽尾部追加 + dispatcher 读/写事实 + target_task_id 保留字段；测试 8 用例（fact_summary 5 + wiring 1 + 会话状态含在内）
- 验证证据：pytest 468 passed / 58 skipped（Docker 守卫）；ruff 全绿；pyright 0 errors；contracts drift=[]；ledger check PASS（3 verified + 1 skipped 环境阻断：真 PG 并发/唯一约束待 Docker 起后验收）；source_purity 新增文件零命中（原基线被其他会话删除，临时基线核对）
- OUT（未碰）：generation_task / task_turn / 多任务消歧 / turn 级版本回退 / 按 turn 分账——grep 无残留；ADR-006 前置未落地
- 下一步：独立 review 子代理结果 → 若 Blocker 清零则 commit；真 PG 集成待 Docker 环境

## 独立 review 结果（最终，Blocker 清零）

- 结论：**Blocker = 0**（Round 2 复审放行）；Should 已处置/登记，无阻塞项。

## 独立 review 报告

- Round 1（全量七维）：Blocker **1** / Should 6 / Nit 3。**Blocker**：`dispatcher.py` 的 `latest()` 无 try/except，PG 读故障冒泡→不 ACK→重投→死信，违反契约"读取失败容错"。
- Fix loop（Round 1 → Round 2）：
  - Blocker 已修：`dispatcher.py` 新增 `_read_fact_summary_with_index()` 容错 helper（唯一生产 `latest()` 调用点在 try 内），读失败返回 (0,"") + warning；三处调用全走容错；补 `test_fact_store_read_failure_is_tolerated` 真断言。
  - Should 1 已修：补 `test_failed_turn_does_not_write_fact`（FAILED 不写事实）；台账证据同步。
  - Should 2 已修：`_write_fact` 包 try/except + logger.warning（store 层仍 fail-closed），主流程 ACK 不阻断。
  - Should 3/4/5/6 已修：payload 改 JSONB（ORM+迁移一致，PG 方言编译验证）；ADR-009 头部改 v0.2；M04 语义注记已加。
- Round 2 复审（全新上下文子代理）：Blocker **0** / Should 3 / Nit 3，**放行**。所有上轮 findings 逐条核验证据（非采信声称）；新发现处置：
  - S1（契约声称 bundle_ref 覆盖但 `SessionRow` 无此列，既有 B4 缺口）：✅ 已修——契约 Why/Invariant 改为诚实措辞，Open 段登记未交付项；ADR-009 §12 IN-4 同步；不写虚假 Invariant。
  - S2（openapi 缺 `target_task_id` 保留字段）：✅ 已修——`openapi.yaml` 补字段 + `pnpm gen:all` 四同步，`contracts:check` drift=[]。
  - N1（读 turn 失败静默→可能 ON CONFLICT 跳过新事实）：✅ 已修——容错 helper 加 `logger.warning`。
  - N2（契约措辞说 Input 增 session_id，实际用 fact_summary 文本字段）：✅ 已修——契约 Delta 改为真实设计。
  - N3（审计 payload 无 from_state）：登记 Nit 不修（属 M04 既有审计语义，扩面会碰其他会话改动）。
- 最终证据：pytest **470 passed / 58 skipped**（Docker 守卫）；ruff 全绿；pyright 0 errors；contracts drift=[]；contract_check PASS；ledger check PASS（3 verified + 1 skipped 环境阻断）；review_gate 见下。

### 门禁补充说明
- removal_check：契约 Delta 为纯新增 + 范围收窄，无带符号名的删除语义 → 脚本 exit 1 表示"无待删符号可核"（非残留 FAIL）；按 cost-tiers「纯新增无废弃项只写一行无遗留留痕」跳过合法。
- verify.py：模板命令（裸 ruff/mypy/src 目录）与项目环境不符，降级为项目实测命令（见台账 evidence）；命令退出码均 0。
- 真 PG 集成：Docker 不可用，台账标 skipped + 环境阻断理由（fact_summary 表并发/唯一约束/fail-closed 待真实环境验收）；不砍功能、不写 fake。

## 追加：本轮 full 档实施收尾（2026-11-22 22:4x）

### 用户四拍板与交付
- 事件 kind 统一 21；ADR-009 三项前置（generation_task/bundle_ref/task_turn）全量补齐；M08 沙箱宿主机直调真 Docker；分阶段多次提交。
- 契约：`.conductor/contracts/2026-11-22/f76a7264-multitask-agent-m08.contract.md`（contract_check PASS / Acceptance=5）；Acceptance 灌台账。

### 独立 review（full 必跑）：Blocker 3 → 0
- Round 1 Blocker=3 / Should=7 / Nit=5。**B1** 规划入口无人触发（server 无 planning 字段）→ `api/server.py` 按 `needs_planning()` 置 planning=1；**B2** 扇出子任务共用父 run_id 撞 UNIQUE → `dispatcher._plan_and_fanout` 每子任务独立 uuid4 run_id；**B3** fan-in 未接线 → `dispatcher._fan_in_if_complete` 全终态才裁决、子任务不各自 complete。
- Should 处置：S4 角色失败短路（不再 run+write_back 撞 FAILED→COMPLETED）；S5 V2 硬拒绝改 fail；S6 task_turn advisory 原子轮次（PgGenerationTaskStore.allocate_turn）；S7 降级 AST 不可用 fail-closed（contract_check 记 violation）；N1/N2/N3/N4 全修。
- 复查证据：`tests/integration/test_m06_fanout_dispatcher_pg.py` 2 passed（真 PG，覆盖 B1/B2/B3）；44 例相关回归通过；全量 **628 passed**。

### 最终门禁证据
- `.venv/Scripts/python.exe -m pytest -q` → exit 0 / **628 passed**（真 PG/Redis/Docker 集成真跑；M08 真 Docker 5 例、多任务 PG 11 例、fan-out 集成 2 例）
- `.venv/Scripts/python.exe -m ruff check .` → All checks passed（exit 0）
- `npx pyright --pythonpath .venv/Scripts/python.exe` → 0 errors
- `pnpm contracts:check` → drift=[]（exit 0）
- `alembic upgrade head` → 0017_session_bundle_ref（真 PG 五表三列 psql 验证）
- `contract_check.py` → PASS（六段 Acceptance=5）；`commitment_ledger.py check --strict` → 22/23（末项=提交，本轮完成后勾销）
- source_purity：Protocol/JSON 动态边界按 purity-exempt-file 标注（router.py/ports.py），M08 JSON 边界用 `JsonValue` 具名别名替代 object；存量 PG 适配层 object/cast 在 HEAD 基线内，本轮未新增污染。

### 分阶段提交（精确暂存；前序会话未提交改动保留不纳入）
- P0 `ff29c95` feat(contracts)：事件 17→21 + 迁移 0015-0017 + 四生成物
- P1/P2 feat(orchestrator)：任务层+角色层+ADR009 接线（含 review Blocker 修复）
- P3 feat(m08)：出护栏+契约校验+真 Docker 沙箱
- P4 docs：ADR/模块文档双向同步 + overview 留痕

### 诚实缺口（不冒充已实现）
1. M08 V3 强制门禁未串进 `run()`：入口 validate_and_test 已真实实现，但 loop 终态无经校验的 WidgetSpec+TSX source_ref+manifest 三入参，缺 M07 结构化生成回路上游（M08 §9 登记 + dispatcher DONE 处注释）。
2. 无真实模型端点：Planner/Generator/Reviewer 生产 fail-fast、测试 mock，多任务端到端真模型验收登记环境阻断（M07 缺口文档逐项）。
3. jsdom 快照为 snapshot.html（DOM 序列化）非像素 png；ADR-004 设备 API/续航预算未做；ADR-008 reasoning SSE tail 未做——均已在对应模块文档登记。

---

## 独立 review 报告（ADR-011 回退）

> 审查者声明：全新上下文独立审查，未采信主代理任何门禁声称，全部命令自行重跑；全程只读，唯一写操作为本报告追加。
> 审查基线：`.conductor/contracts/2026-11-23/f76a7264-rolelayer-rollback.{spec,contract,coverage}.md` + `git status --short` 全部改动文件。

### 0. 门禁证据复核（实测，非采信）

| 门禁 | 主代理声称 | 本次实测 | 结论 |
|---|---|---|---|
| pytest | 661 passed | `.venv/Scripts/python.exe -m pytest -q` → **661 passed**, 9 warnings, 65s, exit 0 | ✅ 属实 |
| ruff | 全绿 | `-m ruff check .` → All checks passed, exit 0 | ✅ 属实 |
| pyright | 0 errors | `npx pyright --pythonpath .venv/Scripts/python.exe` → 0 errors/0 warnings, exit 0 | ✅ 属实 |
| contracts | drift=[] | `pnpm contracts:check` → drift=[], exit 0 | ✅ 属实 |
| alembic | 0018 upgrade/downgrade 闭环 | `alembic -c services/control_plane/migrations/alembic.ini current`（只读）→ `0018_drop_role_layer (head)` | ⚠️ upgrade 已应用属实；**downgrade 闭环本次未实际执行**（只读约束，drop 表/列不可逆且有数据丢失副作用），仅静态审查 → 见 S3 |

### 1. 接线四问（重点核实删除角色分支后的控制流）

- **谁调用它**：`worker.main` 构造 `Dispatcher` 时已不再注入 `_role_runner`；全仓 `role_runner` 在生产代码零命中。`process_one`（`dispatcher.py:752`）是唯一任务消费入口。✅
- **它调用谁**：`run()` 单 agent 内核（`dispatcher.py:817-820`）→ `task_store.update_state`（`:829-834`）→ 按 parent 分流到 `_fan_in_if_complete`（`:839`）或 `_write_back`（`:841-848`）。✅
- **信号被谁消费**：子任务终态由 `coordinator.fan_in`/`emit_fan_in`（`coordinator.py:42/58`）裁决，SSE 前端消费 `done/error/notice`；角色层三种 kind 已删且四生成物一致（见 §3）。✅
- **数据流闭环**：
  - `result_ref`/`reason_code`（`dispatcher.py:826-834`）：DONE → `terminal.bundle_ref`；FAILED → 两者皆 None。原 `role_ref/role_reason` 已随分支消失，无悬空变量（pyright 0 errors 佐证）。闭环成立，但失败原因码丢失 → 见 S4。
  - **"对已 FAILED 会话 complete → ConflictError → 死信"场景**：原角色分支短路的根因是 `role_runner` 内部提前把会话置 FAILED（`dispatcher.py` 旧 `:823-826` 注释）。现在该源头随角色层物理删除而消失；`run()` 是纯内核不碰 SessionService；且 `_fan_in_if_complete` 的幂等守卫（`dispatcher.py:726-728`）对非 RUNNING/AWAITING_INPUT 会话直接返回 True，阻止重投时对已终态会话 `complete`。**结论：走 run() 仍安全，未发现该场景回归。** ✅
  - **fan-in 触发**：`:825-834` 先落子任务终态、`:835-839` 再触发 fan-in，顺序正确；`coordinator.fan_in:50` 要求全部子任务终态才裁决。✅
  - **N=1 零回归**：`_plan_and_fanout` 在 `needs_planning()` 为假时 `:557-558` 直接返回 False 走 `run()` 直路；单 widget 信号不进角色层（角色层本就只在 N>1 注入）。✅

### 2. 七维审查

| 维度 | 结论 |
|---|---|
| 正确性 | 核心链路（接线/终态/fan-in/N=1）正确，无 Blocker；失败原因码丢失属可观测性问题（S4） |
| 错误处理 | `run()` 异常仍留 PEL + 超限死信（`dispatcher.py:849-856`），与角色层删除无关；子任务失败 reason_code 落 None（S4） |
| 并发 | fan-in 幂等守卫在位（`:726-728`）；`coordinator.fan_in` 全终态裁决；未发现本次引入的并发回归（既有 check-then-act 竞态非本次范围） |
| 资源 | 删除两张表 + 黑板乐观锁 + 角色循环，连接与递归开销下降，无新增资源泄漏 |
| 性能 | 子任务少一层模型往返（Gen/Review 返修循环），正向收益；无性能回退 |
| 安全 | 角色消息 hash 链审计（`agent_message`）随表删除消失——属用户明确要求的架构删减，非缺陷；契约无新增注入面 |
| 可维护性 | 生产代码干净、命名自解释（ADR-011 注释到位）；但**契约/规格文档自相矛盾**（S1）+ **文档同步遗漏**（S2）显著拉低新人可懂度 |

### 3. L3 全仓符号残留扫描

- **生产代码**：`RoleRunner/AgentMessage/GeneratorPort/ReviewerPort/ReviewResult/MessageType/MAX_AGENT_TURNS/MAX_REVIEW_ROUNDS/MAX_DELEGATE_DEPTH/AgentSessionState/WorkspaceEntry/WorkspaceConflictError/PgAgentMessageStore/PgAgentWorkspace/GatewayGenerator/GatewayReviewer/run_role_loop/role_runner` —— **零命中**（`pg_stores.py:5` 仅一句"A已移除"说明注释，非死代码）。`roles.py`/`router.py` 物理删除。✅
- **契约与四生成物**：`enums.source.json:11` AgentEventKind=18（无 agent_message/review/delegate）；`events.schema.json:16/44-46` kind 18、无 role/agentId、保留 turn/taskId/taskIndex；`enums.py:61-79`/`enums.ts:28-29`/SDK models.py/schemas.ts/types.ts 同步；`openapi.yaml:670-683` tasks[] 无 role、保留 turn。✅
- **server/models/types**：`server.py:348-349` 无 role、保留 turn；`models_tasks.py` 无 role 列、保留 turn；`agent_core/types.py:884-908` 删 role/agent_id、保留 turn/task_id/task_index。✅
- **文档**：**未通过**——5 个含大量角色符号的文档完全未改、无废弃标注 → 见 S2。

### 4. 红队反例与分级 Findings

**主动找反例结论**：找到了 4 个 Should、3 个 Nit；未找到能推翻"本次回退代码正确且生产代码干净"的 Blocker。

#### Blocker：无（0）

#### S1 — 契约/规格自身多处自相矛盾，验收标准不可信（Should · 文档）
- 位置：`.conductor/contracts/2026-11-23/f76a7264-rolelayer-rollback.contract.md:27` vs `:14`、`f76a7264-rolelayer-rollback.spec.md:58`。
- 后果：Invariants 明写"planner.py:24 的 MAX_AGENT_TURNS import 与 :252 的 __all__ 导出**不许删除**"（states.py 常量本轮保留），而 Delta `:14` 与 spec `:58` 明写"必须同步删除，否则 NameError"；实际代码按后者删了（`planner.py:250` 的 `__all__` 已无该符号，`states.py` 已无该常量）。验收者若照 Invariants 判定会误判实现"违约"；新人照 Delta 改则可能误删 turn 破坏 ADR-009。
- 同类矛盾：contract.md `:15` Delta 把 `turn` 列进"删除 events.py 的 role/turn/agent_id 可选参数"与 `:28` Invariants"turn 必须保留"、实际 `events.py:75` 保留 turn 冲突；`:48` AC-4"本轮零代码改动、git diff 为空"、coverage.md `:10` R4 同款"先出方案"残留，与 AC-1/5/7 要求代码全绿、新增迁移冲突；spec.md `:35-37` 技术栈表"alembic head 0017、不新增迁移"与 `:55` §5.4"加 0018 迁移"冲突。
- 改法：按方案 C 定稿重写 contract.md 的 Delta/Invariants/Acceptance 与 coverage.md 的 R4/AC-4，删除"零代码改动/head 0017/不新增迁移/turn 可删"过时条目，统一口径为：18 kind、head 0018、turn 保留、代码已改。

#### S2 — L3 文档同步遗漏：5 个文件未改未标注，正文仍逐行讲解已删代码（Should · 可维护性）
- 位置（git diff 为空、无任何废弃声明）：
  - `docs/代码精读/01-多任务规划扇出汇总链路.md:47,51,138-188,263-349,991-1122,1455-1716,1751-1879`（逐行讲 `AgentMessage/ReviewResult/GeneratorPort/ReviewerPort/RoleRunner/AgentSessionState/MessageType/MAX_*`、`run_role_loop`、`role_ref/role_reason` 数据流、`roles.py`/`router.py`）
  - `docs/本轮新增代码-全景阅读指南.md:47,52,58,60,77`（21 kind、四表、role/turn/agentId、角色层）
  - `docs/代码精读/05-记忆上下文与会话修正链路.md`、`docs/modules/M07-模型依赖与测试缺口标注.md`、`docs/modules/M15-分通道流式渲染.md`（命中角色符号）
  - 其中 `01`/`05` 是契约 `contract.md:40` Affected 明确列出"需同步"的文件，本轮未同步。
- 后果：契约 AC-9 只 grep "ADR-007 引用处均有废弃标注"，覆盖不到这些文档，所以门禁全绿也发现不了；新人按"代码精读 01"逐行读会指向已物理删除的文件与符号（~2000 行讲解失效），是"全删干净"目标下最刺眼的可维护性断点。
- 改法：为这 5 个文件补废弃/失效声明与 ADR-011 指针；把"精读 01/05"标注为历史存档；对已改的 5 个文档（ADR-006/007/009/整合设计/00，仅 +23 行头部声明）正文里 `ADR-006:41,203,267`、`整合设计:94,113-119` 等仍用 ✅"已同步 kind=21/head=0017/GatewayGenerator 均已改码"的肯定语气，统一改为"历史状态（已废弃）"或去勾选。

#### S3 — 迁移 0018 downgrade 结构与 0016 不一致 + drop 数据不可恢复（Should · 正确性/资源）
- 位置：
  - `services/control_plane/migrations/versions/0018_drop_role_layer.py:54` 用 `sa.JSON()`，而 `0016_agent_collab.py:71` 用 postgresql `JSONB()` → downgrade 后 `agent_message.payload` 从 JSONB 变 JSON（PG 上两者操作符/索引语义不同，且 0018 的 `server_default="'{}'::jsonb"` 与 JSON 列类型不匹配）。
  - `0018:66-67` 用两列 `primary_key=True`（PG 自动名 `agent_workspace_pkey`），而 `0016:107` 用显式 `sa.PrimaryKeyConstraint(..., name="pk_agent_workspace")` → 主键约束名不一致。
  - `0018:28-35` drop 两表 + drop 列本身不可逆，downgrade（`:38-72`）只重建空结构，角色层 hash 链留证数据永久丢失。
- 后果：0018→0017→0018 往返后 schema 与纯 0017 不完全等价；上线后 rollback 不仅数据不可恢复，且角色层代码已删、即便表回来也无法运行，rollback 实际不可行。ADR-011 `:4` 声称"upgrade/downgrade 双向验证"，但仓库无任何自动化测试覆盖 downgrade（`tests/` 仅前置要求 upgrade head），本次只读审查也未实际执行。
- 改法：downgrade 改 `postgresql.JSONB` 并显式 `PrimaryKeyConstraint(name="pk_agent_workspace")` 对齐 0016；在 ADR-011/发布说明明确"0018 单向不可逆、rollback 需连代码一起回退、数据不可恢复"，并把未经自动化的"双向验证"改为"upgrade 已验证，downgrade 仅静态审查"或补 CI 迁移往返测试。

#### S4 — 子任务失败 reason_code 落 None，fan-in 部分失败 notice 丢失失败原因（Should · 错误处理/可观测）
- 位置：`dispatcher.py:833` `reason_code=None` → `coordinator.py:81` `failed_tasks[].reason_code` 取到空串；`coordinator.py:54` 全失败时会话级 reason 固定 `RETRY_EXHAUSTED`。
- 后果：多任务子任务失败时，`run()` 的 `terminal.reason_code`（如 `NO_VERIFIED_BUNDLE`/`MODEL_ERROR`）不落 generation_task，"部分任务未成功" notice 的 `failed_tasks` 无原因码，排障只能翻日志。属契约明确要求（contract.md `:12` 写死 None）但损失诊断信息，spec.md `:46` 又写"直接用 terminal 结果"，两者措辞也不一致。
- 改法：落库改 `reason_code=state.terminal.reason_code if terminal is not None and not done else None`，并把契约 Delta 的"None"改为"terminal.reason_code"，与 spec §5.2 对齐。

#### N1 — 零残留断言用 git grep 且符号清单不全（Nit · 测试）
- 位置：`tests/unit/test_single_agent_path.py:31-39`。
- 后果：只匹配 `run_role_loop/RoleRunner/GatewayReviewer/PgAgentWorkspace` 4 个符号、排除 docs/tests/.conductor，且不覆盖未跟踪文件（如 0018 迁移）；依赖 `.git` 存在，CI 打包无 git 元数据会失败。对"全仓零残留"给出虚假安全感（本次 S2 的文档残留正是它漏掉的）。
- 改法：改为基于 filesystem 的全仓扫描 + §3 完整符号清单，不依赖 git。

## Fix loop（Round 1 findings → 修复证据，2026-11-23）

- **S1 契约/规格自相矛盾** → 已改：contract.md Invariants 改为"删角色常量须同步删 planner 的 import/__all__"、Delta 明确 events.py 只删 role/agent_id 保留 turn、AC-4 改为 reason_code 落 terminal.reason_code；coverage.md R4、spec.md 技术栈表（head 0018）同步。门禁重跑 coverage_check PASS / contract_check PASS。
- **S2 文档同步遗漏（5 文件）** → 已补：docs/代码精读/01、05，docs/本轮新增代码-全景阅读指南.md，docs/modules/M07、M15 全部加 ADR-011 架构更新声明。注：首次用 bash 注入时反引号被 shell 吞掉，已用 Edit 修回完整符号名。
- **S3 迁移 downgrade 不一致** → 已改：0018 downgrade 用 postgresql.JSONB + 显式 PrimaryKeyConstraint(name=pk_agent_workspace) 对齐 0016；实测 downgrade 后用 SQLAlchemy 查 information_schema 确认 payload=jsonb、主键名一致、role 列恢复，再 upgrade 回 0018（head）。ADR-011 §4 补注"drop 数据不可恢复、rollback 需连代码回退"。
- **S4 失败 reason_code 丢失** → 已改：dispatcher.py 子任务终态 reason_code 改为 terminal.reason_code（失败时）/None（成功时）；契约 AC-4 同步。
- **N1 零残留断言依赖 git grep** → 已改：test_single_agent_path.py 改为文件系统全仓扫描（不依赖 .git），符号清单扩到 15 个。
- **修后全量门禁复验**：pytest 661 passed / ruff All passed / pyright 0 errors / contracts drift=[] / alembic 0018（downgrade→upgrade 闭环 + 结构验证）。

### Round 2 收口

- 独立复审（全新子代理）结论：**Blocker=0 / Should=1 / Nit=3**。S1-S4、N1 逐条核实：S2/S3/S4/N1 已修复（S3 复审者亲自执行 downgrade 查 information_schema 确认 payload=jsonb、PK=pk_agent_workspace、role 列恢复）；S1 部分修复——spec.md 三处旧口径残留。
- S1 残留已修：spec.md 技术栈摘要改 alembic 0018 + pg8000（顺带纠正 psycopg事实错误）；§3 范围/范围外改为方案 C 口径；§12 验收改"0018 downgrade 闭环"；contract.md Delta 的 reason_code 口径统一。重跑 coverage_check PASS / contract_check PASS。
- Nit 3 条留痕不修：contract 测试符号清单 16 个（文档写 15，无功能影响）、扫描 scope 不含 infra/（角色符号本就不在 infra）、纯子串匹配可能命中历史留痕（当前无）。
- **Blocker 连续两轮为 0 → review 门禁放行，进收尾。**

#### N2 — 新增测试未直接验证 run() 终态→落库→fan-in 的接线（Nit · 测试）
- 位置：`tests/unit/test_single_agent_path.py:17-50`（3 个测试分别查签名、grep 符号、任务层符号存在）。
- 后果：AC-1 要求"无 role_runner 时 dispatcher 经 run() 产出终态与会话 done"，但新增测试没有真正跑通一次 `process_one` 的 run→update_state→fan-in 闭环；该保证依赖既有 `test_m06_fanout_dispatcher_pg.py`（AC-2，确实全通过）。若后续有人误改 `dispatcher.py:826-848` 的终态/分流，新测试不会红。
- 改法：补一个 mock 端口的 `process_one` 单测，断言子任务 DONE/FAILED 时 `result_ref/reason_code` 与 `_fan_in_if_complete` 调用。

#### N3 — 已删角色的 SSE 事件向前兼容未声明（Nit · 契约）
- 位置：`packages/contracts/events.schema.json:5,14-16`（description 称"未知 kind 容忍扩展 I6"，但 `kind.enum` 强列 18 值，顶层 `additionalProperties:false`）。
- 后果：Redis Stream 历史窗口（最近 500 条）里若仍有删除前的 `agent_message/review/delegate` 或带 `role/agentId` 的事件，新 schema 会判为非法；旧消费者/重放路径在无兼容层的前提下无法处理。属有意 breaking change，但文档未声明迁移期处理策略。
- 改法：在 ADR-011/契约加一句"kind 21→18 为破坏性变更，历史窗口旧事件由前端 reducer 忽略（或清空窗口），无兼容层"。

### 5. 结论

- **Blocker = 0**：本次回退在代码层正确、干净，接线四问全部闭环，N=1 零回归，门禁五项实测属实（downgrade 闭环本次未实际执行、仅静态审查并发现 S3）。
- **Should = 4**（S1 契约矛盾 / S2 文档遗漏 / S3 迁移可逆性 / S4 失败原因码），**Nit = 3**（N1/N2/N3）。
- 建议放行前至少收敛 S1（否则验收口径自相矛盾）与 S2（否则"全删干净"在文档层破产）；S3/S4 可排入紧后版本。


## 独立复审报告（Round 2）

**复审方式**：全新上下文只读独立核实；唯一写操作为把本报告追加到本 overview。所有结论以当前工作区文件与本人实测为准，未采信开发者声称。

### 0. 总结论

- **Blocker = 0**
- **Should = 1**（S1′ 契约三件套未真正统一：spec.md 仍有 3 处方案 A 残留矛盾）
- **Nit = 3**（详见 §4）
- 全量门禁实测全绿：pytest 661 passed / ruff All checks passed / pyright 0 errors / pnpm contracts:check drift=[] / alembic current = 0018_drop_role_layer (head)。

### 1. 逐条核实

#### S1 契约自相矛盾 → 部分修复（Should）
- 已落地：contract.md 的 Invariants(:27)、Delta(:14-15)、AC-4(:48) 已统一为方案 C；coverage.md R4(:10)/R6(:12)/R7(:13) 已统一。Round 1 点名的"MAX_AGENT_TURNS 不许删""events.py 删 turn""零代码改动"三处已消除；contract.md:3 明确"18 kind / head 0018 / turn 保留 / 代码已改"。
- 未落地（残留）：spec.md 仍是方案 A 口径，3 处与方案 C 直接冲突：
  1. spec.md:5 技术栈摘要仍写 `PostgreSQL (psycopg) / ... alembic 0017`（应为 head 0018；且项目不装 psycopg，见 tests/support/pg.py）。
  2. spec.md:27 §3 范围外仍列"物理删除 roles.py/router.py、删除两张 DB 表、改事件 kind 枚举、**加 0018 迁移**、重生成 SDK"——而 spec.md:55 §5.4 与 :66 方案 C 明确这些全部在范围内并已执行。
  3. spec.md:77 §12 仍写"核心：...、**无新迁移**、角色层零调用"——与已加 0018 冲突。
- 后果：契约三件套口径未真正统一。验收者按 spec.md §3/§12 判定会把已执行的方案 C 范围项误判为"越界"；新人按"范围外"表述会遗漏 0018 迁移是本次交付物。"已统一为方案 C 口径"的声称对 contract/coverage 成立、对 spec 不成立。
- 改法：spec.md:5 → `alembic head 0018`（并去掉 psycopg）；:27 删除"范围外"整行或改为"方案 C 范围内且已执行"；:77 "无新迁移" → "0018 迁移已加"。

#### S2 文档同步遗漏 → 已修复
- 5 个文件第 3 行均有完整 ADR-011 架构更新声明，符号名完整、无残缺的"、/、（空"：
  - docs/代码精读/01-多任务规划扇出汇总链路.md:3（AgentMessage / ReviewResult / GeneratorPort / ReviewerPort / RoleRunner、run_role_loop、role_ref/role_reason、roles.py / router.py 全部完整）
  - docs/代码精读/05-记忆上下文与会话修正链路.md:3（RoleRunner / 角色分支注入）
  - docs/本轮新增代码-全景阅读指南.md:3（kind=18、head=0018、role/turn/agentId）
  - docs/modules/M07-模型依赖与测试缺口标注.md:3（Planner/Generator/Reviewer）
  - docs/modules/M15-分通道流式渲染.md:3（agent_message / review、21→18）
- git diff 核实：全景指南/M07/M15 各仅 +3 行（声明 + 2 空行），未破坏正文其他部分；01/05 所在 docs/代码精读/ 整目录为未跟踪文件（??），声明内容完整。
- 其余已改文档（ADR-006/007/009/整合设计/00）均有 ADR-011 头部声明并明确"历史表格保留作演进留痕"，正文 ✅ 留痕属有意为之，不再单独记问题。

#### S3 迁移 0018 downgrade → 已修复（含本人实测）
- 代码：0018_drop_role_layer.py:16 `from sqlalchemy.dialects.postgresql import JSONB`、:55 `payload JSONB()`、:73 `PrimaryKeyConstraint("session_id","key", name="pk_agent_workspace")`、:42-43 重建 generation_task.role —— 与 0016:71/107 完全对齐。
- 本人实际执行（DSN=DATABASE_URL 指定的 asyncpg，alembic -c services/control_plane/migrations/alembic.ini，查库用 .venv/Scripts/python.exe + SQLAlchemy pg8000）：
  1. alembic current → `0018_drop_role_layer (head)`；head 态 tables=[generation_task]、generation_task 列=[turn]（两表与 role 列已删）。
  2. alembic downgrade 0017_session_bundle_ref → 成功（Running downgrade 0018_drop_role_layer -> 0017_session_bundle_ref）。
  3. downgrade 后 information_schema 实测：
     - agent_message.payload：`data_type=jsonb, udt_name=jsonb` ✓
     - agent_workspace 主键：`conname=pk_agent_workspace` ✓
     - generation_task：`role=text, turn=integer`（role 恢复、turn 保留）✓
     - 索引：`agent_message_pkey, ix_agent_message_session_task, ix_agent_message_task_rev, pk_agent_workspace` ✓
  4. alembic upgrade head → 成功（Running upgrade 0017_session_bundle_ref -> 0018_drop_role_layer）；current 回 `0018_drop_role_layer (head)`；复核 tables=[generation_task]、列=[turn]。闭环成立。
- 数据不可恢复限制已在 ADR-011:40 明确记载。

#### S4 reason_code → 已修复
- dispatcher.py:829-836：子任务终态 `update_state(..., result_ref=terminal.bundle_ref if done else None, reason_code=terminal.reason_code if terminal is not None and not done else None)` —— 失败落 terminal.reason_code、成功落 None。
- 落库链路完整：ports.py:149 签名含 reason_code；pg_stores.py:227-228 写入 values；:111/:312-313 读回；dispatcher.py:716 注入 TaskResult；coordinator.py:69/:81 进入 done results[] 与 notice.failed_tasks[].reason_code。
- 红队核实：改动未破坏 fan-in notice —— 失败原因码现在能传到 notice.failed_tasks[].reason_code，较修复前信息更完整；coordinator.py:78 会话级 code 仍固定 RETRY_EXHAUSTED（既有行为，非本次引入）。

#### N1 零残留测试 → 已修复
- test_single_agent_path.py:27,42-49 改为 Path.rglob 文件系统全仓扫描，不依赖 git/git grep，CI 无 .git 可跑；:30-31 检查 roles.py/router.py 物理删除；:16-19 检查 Dispatcher 无 role_runner 参数。
- 符号清单实测为 16 条（:34-39：14 个符号名 + 2 条 import 路径），开发者声称"15 个"计数不符（见 Nit N2′）。
- 实测 pytest -q 通过（661 passed）。

### 2. 全量门禁重跑

| 门禁 | 命令 | 结果 |
|---|---|---|
| pytest | `.venv/Scripts/python.exe -m pytest -q` | 661 passed, 9 warnings in 73.38s |
| ruff | `.venv/Scripts/python.exe -m ruff check .` | All checks passed! |
| pyright | `npx pyright --pythonpath .venv/Scripts/python.exe` | 0 errors, 0 warnings, 0 informations |
| contracts | `pnpm contracts:check` | OK —— 生成物与契约源一致（drift=[]） |
| alembic | `... alembic -c ... current` | 0018_drop_role_layer (head) |

### 3. 红队视角（修复是否引入新问题）

- Edit 修反引号：git diff 核实全景指南/M07/M15 各仅新增 3 行（声明 + 空行），正文逐行讲解未被破坏；01/05 声明符号完整。未发现破坏。
- reason_code 改动：未破坏 fan-in notice，反而补齐失败原因码；无回归。
- 文件系统扫描：未误报（661 passed），但存在覆盖缺口（见 Nit N2′/N3′）：不扫 infra/（1 个 py，当前无命中）、清单未含 GeneratorPort/ReviewerPort/AgentMessage/ReviewResult/Router/self._role_runner（当前均已删净）、子串匹配可能把未来注释/文档里的历史符号名当命中。
- S1 修复过程：contract.md/coverage.md 改好了但 spec.md 未同步，属修复不完整，非引入新代码问题。

### 4. 分级结论

#### Blocker = 0

#### Should（1 条）

**S1′ — spec.md 残留方案 A 旧口径，契约三件套未真正统一（Should · 文档/验收口径）**
- 位置：spec.md:5（alembic 0017 + psycopg）、:27（范围外含"加 0018 迁移"）、:77（"无新迁移"）。
- 后果：方案 C 已执行（18 kind / head 0018 / 0018 迁移已加），但 spec 仍声明这些在范围外或未发生；验收者按 spec 判定会误判实现越界，新人按"范围外"会遗漏 0018 迁移是本次交付物；psycopg 与项目实际（pg8000，见 tests/support/pg.py）不符。
- 改法：:5 改 `alembic head 0018` 并去掉 psycopg；:27 删除"范围外"整行或改为"方案 C 范围内且已执行"；:77 "无新迁移"改"0018 迁移已加"。

#### Nit（3 条）

**N1′ — contract.md:12 Delta 仍写 reason_code 改用 None（Nit · 文档残留）**
- 位置：contract.md:12 "`result_ref`/`reason_code` 改用 `terminal.bundle_ref`/`None`" vs contract.md:48 AC-4 与 dispatcher.py:833-835 实际（失败落 terminal.reason_code）。
- 后果：Delta 描述与 AC-4/代码不一致；Round 1 S4 改法要求"把 Delta 的 None 改为 terminal.reason_code"，开发者只改了 AC-4 和代码，Delta 未同步；读者按 Delta 会以为失败也落 None。
- 改法：:12 改为"`result_ref` 取 `terminal.bundle_ref`（DONE）/`None`（失败），`reason_code` 取 `terminal.reason_code`（失败）/`None`（成功）"。

**N2′ — 零残留测试清单计数与覆盖缺口（Nit · 测试）**
- 位置：test_single_agent_path.py:34-39（forbidden 16 条：14 符号名 + 2 import 路径，声称"15 个"）；:42-43 仅扫 services/packages，未扫 infra/（含 1 个 py）。
- 后果：声称的"15 个符号清单"与实际 16 条不符；未覆盖 GeneratorPort/ReviewerPort/AgentMessage/ReviewResult/_REVIEW_SYSTEM/Router/self._role_runner（当前均已删净，但清单不含则未来复发漏检）；infra 未纳入扫描。
- 改法：把上述符号补进 forbidden 并统一计数口径；base 增加 `infra`。

**N3′ — 符号扫描为纯子串匹配，可能误报历史留痕（Nit · 测试健壮性）**
- 位置：test_single_agent_path.py:46-49（`if symbol in text` 扫全文，含注释与 docstring）。
- 后果：docs/代码精读/01 等历史文档明文保留 RoleRunner/MAX_AGENT_TURNS 作留痕；若将来把扫描范围扩到 docs，或生产代码注释里写"已删除 RoleRunner"，会误报失败。当前范围（services/packages）无此问题。
- 改法：限定代码 token 匹配（如 AST 或正则词边界），或显式排除注释/docstring。

### 5. 最终结论

- **Blocker = 0**，本轮代码层修复真实落地、全量门禁实测全绿，S3 downgrade 闭环经本人实库验证通过。
- 需收敛 Should=1（spec.md 三处旧口径），否则契约三件套口径仍不自洽；Nit=3 建议一并清理。

## 追加：6 份代码精读文档同步 ADR-011（2026-11-23 10:0x）

- 用户要求：全部 6 份代码精读「更新行号 + 清理失效内容，主要删除多 agent 架构」。文档 6 份近 9700 行，按"行号失效只发生在源码被改动的文件"分诊：01/02 重灾区、05 少量、03/04/06 源码未变只校准。
- 并行处理（4 个独立子代理，各改各文件无冲突）：① 01（1920→1682 行，删角色层段落 + 章节重编号 + 全套行号）；② 02 重写（963→555 行，从「多Agent角色协作与任务存储」改为「任务存储与事件链路」，只留 generation_task/task_turn/事件链路/装配/状态机）；③ 05（清 7 处角色引用 + dispatcher/server 行号偏移）；④ 03/04/06（server.py 行号、types.py 总行数、output_ports/validators/sensitive 行数校准）。
- 行号核对方法：实际 Read 源码 + wc -l，逐段定位函数真实行号。关键锚点与源码吻合：ports 169 / states 45 / events 102 / planner 259 / model_ports 93 / pg_stores 371 / dispatcher 876（_plan_and_fanout 541、_write_task_turn 650、_fan_in_if_complete 688、process_one 752、run_forever 861）；02 纠正了 SSE 位置（RedisEventSource.since 真实在 control_plane/sessions/sse.py:60，不在 agent_core/events/sse.py）。
- 最终验收：6 份文档角色层符号全仓 grep **0 残留**（hash 链/黑板命中均为 M08 证据链、M07 记忆偏好链等合法保留语义）；系列说明统一为「02 任务存储与事件链路」；行数标注脚本核对 30 处一致、修正 4 处偏差（output_ports 193→192、validators 39→38、sensitive 96→95）。
- 文件名（用户 2026-11-23 追加决定）：02 已从 `02-多Agent角色协作与任务存储链路.md` **重命名为** `02-任务存储与事件链路.md`，并同步 4 处引用（01 链接、contract Affected、overview 本条、工作日志）。内容早已重写为任务存储主题，文件名随之一致。
- 本轮纯文档改动，无代码变更，未重跑 pytest（上一轮代码门禁 661 passed 仍有效）。
