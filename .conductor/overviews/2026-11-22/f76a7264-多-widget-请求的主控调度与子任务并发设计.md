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
