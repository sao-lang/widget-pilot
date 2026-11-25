# 确定性 widget 生成通道 · 设计规格 v1.0（完整版，用户授权拍板）

**状态**: 已确认（2026-11-25 13:32 用户明确「完整、功能完善、不做简化版、具体方案你定」→ U-1～U-14 按本文定稿执行；不再有「首版先做简单版」的裁剪）
**范围**: 新增一条与 LLM 通道并行的确定性生成通道：单 widget 与多 widget（N>1）均可零 LLM 调用地规划、生成 WidgetSpec + TSX、过真三门禁、真成包、真终态；解析不了或门禁失败时 fail-closed 或回落 LLM，绝不造假产物。
**技术栈摘要**: Python 3.13 / 现有 contracts schema / PG + Redis + S3 端口 / podman 沙箱 / 零新增依赖
**关联**: ADR-004 去类型化、ADR-006 多任务扇出、ADR-010 设备 API、M03 loop、M06 校验链、M08 出护栏与沙箱、M09 成包
**变更类型**: 补充设计（新通道，不替换 LLM 通道）

---

## §1 设计意图

- **一句话意图**：让「描述能确定性解析」的请求——**包括一次生成多个 widget**——全程零 LLM 调用地产出与 LLM 通道**同门禁、同成包、同终态**的真实 widget；解析不了就回落，解析得了但门禁不过就 fail-closed，绝不用占位产物骗终态。
- **设计原则**（可判定）：
  1. **完整双通道**：确定性通道覆盖 N=1 与 N>1 两条路径；多 widget 的规划也由确定性 `PlanLLM` 实现承担，不是只做单卡片。
  2. **门禁一视同仁**：模板产物过与 LLM 产物**完全相同**的三门禁（出护栏六判据 + manifest 契约 + 真沙箱），不因来源豁免。
  3. **不碰 LLM 内核**：`run()` 的 `CALL_MODEL` 分支、`Deps.model` 必填、`decide()` 的 `CALL_MODEL` 默认分支全部不动；分流在 dispatcher 层，确定性规划通过 `PlanLLM` Protocol 注入，不改 `run_fanout_planning`。
  4. **终态必须是真产物**：DONE 带真实 `bundle_ref`，来自 `assemble()`；N>1 走真 fan-in，父会话在所有子任务终态后才裁决 COMPLETED。
  5. **成本诚实**：tokens=0 但沙箱实耗/tool_calls 照记，预算触顶在 compile 前 check。
  6. **确定性可复现**：WidgetSpec 全 16 字段、TSX 字节、manifest 全部是 (description, grid, 模板登记表版本) 的纯函数；同输入必得同 hash。
- **价值排序**：产物真实性 > 门禁等价 > 完整覆盖（含多 widget） > 分流准确率 > 实现简洁。
- **反意图**：不引入 NLU/小模型；不承诺开放域描述都能生成；不替换/削弱 LLM 通道；不跳过任何 M08 判据；不做「只支持 3 类静态卡片」的简化首版。
- **与既有设计意图的关系**：沿用 ADR-004 去类型化（模板选择按 `data_contract`/槽位，不按 `WidgetType`）；沿用 ADR-006 的 PlanLLM Protocol + PlanValidator fail-closed 结构，只替换「谁来下结论」——确定性实现取代模型实现，**不改校验器**；不撤销任何既有非目标。

## §2 背景与目标

**现状（源码核实）**：
- 意图级联 **L1 恒零模型**（`intent_cascade.py:53-73`）；L2/L3/L4 是**可选模型端口**（`:168-186`/`:108-124`/`:207-234`），生产装配层显式不注入（`guardrail_adapter.py:8-10`）才默认零模型。是分流器，不是生成器。
- `run.py:280` 是唯一模型调用点；`decide.py:95-97` 默认分支即 `CALL_MODEL`；`deps.py:270-277` 缺模型配置 fail-fast。
- 多 widget 规划 `GatewayPlanLLM.plan`（`model_ports.py:40-56`）调 `self._gateway.complete`；`run_fanout_planning(facts, llm: PlanLLM)`（`fanout.py:111`）接受 Protocol，可注入确定性实现。
- WidgetSpec 16 必填字段（`widget_spec.schema.json:8-12`）生产**无构造点**；`apply_model_output` 白名单不含 `spec/extras`（`run.py:46-50`）。
- `validate_and_test`（`entry.py:290-305`）与 `BundleService.assemble`（`service.py:131-141`）**只有测试调用点**。
- TSX → `source_ref` 的生产 writer 不存在，只有 `SourceReaderPort` 读端口（`output_ports.py:154`）。
- `sandbox_minutes` 生产恒 0（`types.py:139-149` 结构支持，无写入点）。

**动机**：用户指出「意图识别只是 LLM 挡位分流器，没有真实不走 LLM 的链路」，并明确要求完整方案（不简化）。本设计补齐 N=1 与 N>1 两条真实零 LLM 链路，同时在确定性通道上首次接通 M08 三门禁与 M09 成包（**仅此通道；LLM 通道仍缺，不冒充全量**）。

**成功标准**：
- **N=1**：单 widget 描述从入队到 COMPLETED 全程 `model.complete` 调用次数 0；终态 `bundle_ref` 指向 PG 真 bundle；沙箱实耗入账。
- **N>1**：多 widget 描述（如「桌面上加个时钟和待办清单」）由确定性 `PlanLLM` 拆成 2 子任务，父会话 fan-in 后 COMPLETED；父任务规划与子任务生成全程 `model.complete` 调用 0；每个子任务各自真成包。
- **回落**：歧义描述走原 LLM 路径，行为与今天一致。

## §3 范围与边界

- **范围内**：模板登记表（全场景，见 §7.4）、槽位抽取、确定性 `PlanLLM`、WidgetSpec 构造器、TSX 确定性渲染、source_ref writer、dispatcher 双分流点、三门禁调用、成包、N=1/N>1 终态与后置义务、回落、fail-closed、可观测指标、模板版本门禁。
- **范围外**：改 `run()`/`decide()`/`Deps.model` 的模型分支；改公共契约 schema；修 `events.schema.json` 的 `oneOf` 漏判（独立议题，本通道不依赖它）；为 LLM 通道串 M08 门禁；真实模型端点；设备 API 运行时调用（ADR-010 P-next）。
- **反向排除**：不把开放域问答纳入；不把需外部 API 的实时数据（天气/股票/快递）当作已实现功能——这类只能出**静态占位骨架**且须在 spec 的 `data_contract.sources` 中标 `static`，不得假装实时；不把未过门禁的模板产物标 DONE；不把 `widget_type` 当模板选择键。

## §4 技术栈

| 类别 | 选型 | 理由 |
|---|---|---|
| 语言 / 运行时 | Python 3.13 | 与仓库一致 |
| 契约 | `widget_spec.schema.json` / `widget_bundle.schema.json` | 单一事实源，不改形状 |
| 规划 | `PlanLLM` Protocol（`tasks/ports.py:96-103`）+ `PlanValidator`（`planner.py:89`） | 复用 ADR-006 fail-closed 校验，只换实现 |
| 存储 | PG（bundle/audit/cost_ledger）、S3（TSX 对象） | 复用 `S3EvidenceStore` 真端口 |
| 沙箱 | `validate_and_test` + podman runner | 复用 M08 真隔离 |
| 测试 | pytest + 真 podman 沙箱 + mock `ModelGateway` 计数 | 证明零调用 |
| 新增依赖 | 无 | YAGNI |

## §5 需求清单

### 5.1 规划与分流

- **FR-1**：`deterministic.TemplateRegistry`：登记表按 `(数据契约族, 宿主/动作语义族, 能力白名单子集)` 索引；每模板有 `render(slots) -> bytes`、`spec_fields(slots)`、版本号。验收：全场景模板表命中矩阵测试。
- **FR-2**（v1.2 修正：grid 不再要求「描述里显式」，而是复用系统现成的澄清闭环）：`deterministic.try_compile(description, grid, fields) -> CompileResult | None`。
  - **grid 来源三态**：① 选择器传入（`GridSpec`）→ 直接编译；② 描述字面命中 `GRID_HINT_PATTERNS`（`grid_resolver.py:95-102`）→ 直接编译；③ **grid 缺失 → 不回落 LLM，走现有 `SPEC_MISSING` 澄清**（`semantic.py:113-118` → dispatcher 转 `AWAITING_INPUT` → 用户答 `4x4` → `clarified_grid` + `grid_confirmed=True` 跳过描述充分性复检，`validate_chain.py:275-284`）→ 用户答完重入时用 `clarified_grid` 编译。**只有用户在澄清里也答不出/取消才回落 LLM。**
  - **data_contract 与意图**：必须确定且在模板登记表（§7.4）；歧义 → None 回落。
  - 验收：三类 grid 来源都能编译；grid 缺失用例断言进 AWAITING_INPUT 而非回落；澄清重入用例断言用 clarified_grid 编译成功。
- **FR-25**（v1.2 新增：提高命中率的组合能力）：**长尾/口语化描述**先过现有 L1/L2/L3 意图级联得出 `intent_normalized`（零模型或本地 embedding），只要 `intent=WIDGET_GENERATION` 且 data_contract 可确定，即便措辞不在「动作×宿主」字面模板内（如「小玩意」），也允许进入编译——**编译期的宿主锚点从 L1 命中改为 `data_contract` 词族 + 已确认 grid 双锚定**，避免为召回改 L1 词表破坏 ADR-004 硬样本。route_layer 统计仍由原级联记录，不受编译影响。验收：ADR-004 三条硬样本原样绿；「给我整一个能显示日期的小玩意，2x2 的」在 grid 明确后能编译。
- **FR-3**：`DeterministicPlanLLM(PlanLLM)`：实现 `plan(facts, repair_errors) -> TaskPlanDraft`，从 `facts.original_description` 确定性拆分多 widget（连接词 + 宿主/对象语义族；拆分出的每条 description 必须能被 FR-2 命中），走 `run_plan`→`PlanValidator` 同款校验，不调 `model.complete`。验收：N=2/3 的规划 draft 严格合法，mock gateway 计数 0。
- **FR-4**：分流点一（N=1）：`dispatcher.process_one` 在 `_check_guardrail` 放行后、`_plan_and_fanout`/`run()` 前调用 `try_compile`；条件 `validation is not None and validation.ok and task.planning != "1" and task.resume != "1" and not task attachments and not needs_planning(description)`（`dispatcher.py:507-508,557,770-775`）。验收：命中路径 `model.complete` 计数 0。
- **FR-5**：分流点二（N>1）：`needs_planning(description)` 为真时，把 `self._plan_llm` 替换为注入的 `DeterministicPlanLLM`（若全子任务可编译）后走原 `_plan_and_fanout`；若任一子任务不可编译 → 回落原 `GatewayPlanLLM`。验收：可编译多 widget 全程 gateway 计数 0；不可编译回落计数>0。
- **FR-6**：回落判据全局唯一：由 `try_compile` 的 None 与 `DeterministicPlanLLM` 的「全子任务可编译」共同决定；禁止在 dispatcher 里散落第二套判据。验收：判据函数单测 + 调用点 grep。

### 5.2 产物构造

- **FR-7**：WidgetSpec 构造器填齐 16 必填字段，`widget_type=custom`，`language=tsx`，`import_allowlist` 固定；**全字段为 (description, grid, registry_version) 纯函数**，`widget_id=uuid5(NAMESPACE_URL, spec_hash)`。验收：产物过 `SPEC_SCHEMA`；同输入两次 canonical_json 一致。
- **FR-8**：TSX 模板骨架 + 槽位填空，同槽位必得同字节（不含时间戳/随机/id）。验收：两次渲染 sha256 一致。
- **FR-9**：manifest_draft 填全 13 必填字段，口径：`model=deterministic-template:<registry_version>`、`prompt_version=<模板版本>`、`model_tier=cheap`、`retrieval_snapshot=template-registry@<registry_hash>`、`spec_hash=sha256(canonical_json(spec))`（`contract_check.py:38-40,88-92`）。验收：`check_contract` 通过，无占位/假声明。
- **FR-10**：source_ref writer 写 S3 并返回 `key#sha256:{64hex}`（`output_ports.py:182-184`），key `code/{session}/{run}/widget.tsx`。验收：读回 hash 校验通过。

### 5.3 门禁与成包

- **FR-11**：`validate_and_test(spec, source_ref, manifest_draft, repair_count, *, session_id, run_id, evidence_store, ...)` 真调用（完整签名 `entry.py:290-305`，含第 4 位置参与 3 必填 kw），三门禁全过才继续；传参绕过 `_RESULT_CACHE`（`entry.py:311-319`）保证真跑一次。验收：真 podman 沙箱用例 passed；缓存不短路用例。
- **FR-12**：`BundleService.assemble` 真调用产出 bundle；`bundle_ref = bundle["bundle_id"]`（与 `Terminal.bundle_ref` 及 `_fan_in_if_complete:742-743` 口径核对后定）；`bundle_id` 每次新生成（uuid4），**可复现的是产物 hash 而非 bundle 身份**。验收：PG 查得该 bundle 行。

### 5.4 终态与后置义务（红队 3.4 修正：共享而非复刻）

- **FR-13**：把 `dispatcher.process_one` 中 `run()` 之后的公共后置义务抽成 `_finalize_process(...)`（`:821-850`）：`_write_task_turn` / `task_store.update_state` / `_fan_in_if_complete`(N>1) 或 `_write_back`(N=1)；正常路径与确定性路径**共同调用**该函数，禁止复刻。验收：两路径的后置行为用同一组测试断言。
- **FR-14**：终态用 `state.terminate(Decision(DecisionKind.DONE), now=clock.now(), bundle_ref=<真实>, evidence_ref=<...>)`（`types.py:386-391` 强制 DONE 带 bundle_ref；`State` 无顶层 signal，signal 在 `State.spec.signal`:92）。验收：会话 COMPLETED 且 bundle_ref 非占位串。
- **FR-15**：确定性路径仍走租约获取/释放（`run.py:247-249,437-442`）、`_persist` rev+hash 链（`:380-382`）、CHECKPOINT/COST/DONE 事件 seq 连续（`:384-412`）——通过复用同一 finalizer 与事件工具实现，不重写。验收：无 PEL 重投、事件 seq 连续。
- **FR-16**：`state = meter.add(state, sandbox_minutes=<实耗>, tool_calls=1)` 后**把新 State 交给 `cost_sink(state)`**（`meter.add` 返回新 State，`accrue` 读 `state.budget_used`），tokens=0，`model_tier` 记 `deterministic-template`。验收：cost_ledger 行 sandbox_minutes>0、tokens=0。
- **FR-17**：compile 前 `meter.check(state)`，预算触顶转 FAILED(`BUDGET_EXHAUSTED`)，不进沙箱（`run.py:267`）。验收：触顶用例断言不进沙箱。

### 5.5 失败、边界、可观测

- **FR-18**：门禁失败**不回落**，fail-closed 转 FAILED（reason_code + evidence_ref）+ error 事件 + audit；不新增 guard_hit 写入点（该表属输入侧）。验收：门禁失败用例断言终态与留证。
- **FR-19**（v1.1 修正：原「带附件一律回落 LLM」是过度保守——附件处理全是纯代码，零 LLM）：**附件可确定性编译**，回落判据只有三条——① `row.role is None`（role 歧义，`role_resolver.py:41,64`）；② `row.insight_ref` 缺失（未生成洞察）；③ 附件 role 与模板能力冲突（如 `visual_reference` 命中只接受静态骨架的模板、`data_source` 不得伪装实时）。三项全过 → 从 PG 的 `AttachmentRow` 直接填 `spec.attachments[{attachment_id, role, insight_ref}]`（`widget_spec.schema.json:19-30`），**不调任何模型**。OCR 文本若注入 `layout_slots`，其 PII 由出护栏 `_pii_violations`（`output_guard.py:165-180`）真值校验拦住，命中即 fail-closed。验收：role 明确 + ref 存在的附件走确定性路径且 gateway 计数 0；role 歧义/缺 ref/能力冲突三种回落。
- **FR-24**（v1.1 新增：多 widget + 附件的事实注入）：`_plan_and_fanout` 当前把 `attachments=()` 写死（`dispatcher.py:569`），确定性 `PlanLLM` 看不到附件。修正为从 `attachment_get` 把 task 的 `attachment_ids` 解析成 `AttachmentFact` 注入 `PlanningFacts`（`planner.py:50-86`），由 `TaskDraft.attachment_ids` 分配给子任务，`PlanValidator`（`planner.py:104-118`）校验 id 不幻觉；**归属歧义（一个附件与多个子任务描述都强相关）→ 整单回落 LLM，不猜**。验收：N=2 + 1 附件的确定性拆分用例，附件只进对应子任务。
- **FR-20**：`clarification_reply=1` 的 grid 取 `clarified_grid or grid`；澄清重入若槽位仍不全 → 回落；`resume=1` 一律不走确定性通道（续跑语义）。验收：两种重入路径单测。
- **FR-21**（v1.2 修正）：模板命中**不改变 L1/L2/L3 的 route_layer 统计**，也不改 `INTENT_SAMPLES_VERSION`；长尾措辞的命中由 **FR-25** 的「data_contract + grid 双锚定」承担，不靠改 L1 词表——因此 ADR-004 硬样本「给我整一个能显示日期的小玩意」在 L1 仍是未命中走 L2/L3，**只是在 grid 明确后额外获得编译资格**。验收：`test_a25_route_layer_logged` 与 ADR-004 三条硬样本原样绿。
- **FR-22**：模板登记表版本化：版本号进 spec 指纹与 `change_gate.ConfigSnapshot`；新增/修改模板需 review 并升版本；旧模板产物可按版本号复现。验收：`diff_config` 非空；旧 hash 可重放。
- **FR-23**：可观测性：`generation_path`、`命中/回落/门禁失败`计数写**日志 + 独立指标**，不改事件 schema（`events.schema.json` payload `oneOf` 漏判是独立议题）。验收：日志/指标可查。
- **NFR-1**：不新增依赖；不改 `run()`/`decide()` 的模型分支。验收：git diff 不含这两处。
- **NFR-2**：模板产物不得含动态 API / 禁用 import；验收：出护栏 forbidden/danger 判据零命中。

## §6 Approaches

| 方案 | 做法 | 优点 | 代价 | 结论 |
|---|---|---|---|---|
| A 完整双通道（本方案） | dispatcher 双分流 + 确定性 `PlanLLM` + 模板产物走真门禁/成包/fan-in | N=1/N>1 都零 LLM；不改 LLM 内核；产物真实；复用 ADR-006 校验 | 模板集有限；需补 writer；确定性规划有边界 | **采用** |
| A' 简化首版（仅 3 类静态模板、仅 N=1） | 先做最小集再扩 | 快 | 用户明确否决「先做简单版」；多 widget 仍调 LLM | 否决 |
| B `ModelGateway` 假实现 | 注入不联网的 gateway 返模板 | 零改 loop | 违反「模型网关必真实」红线；污染 `Deps.model` 语义 | 否决 |
| C LLM 生成后缓存复用 | 首次调 LLM 再模板化 | 覆盖广 | 首次仍调 LLM，无法证明零调用 | 否决 |

## §7 架构设计

### 7.1 完整分流与数据流

```
dispatcher.process_one
  → _check_guardrail → 放行？(否则原路径)
  → needs_planning(description)?
      ├─ 是（N>1）：DeterministicPlanLLM.plan(facts)  [FR-3]
      │    全子任务可编译？─ 否 → 回落 GatewayPlanLLM → 原 _plan_and_fanout
      │    是 → 原 _plan_and_fanout(facts, DeterministicPlanLLM)
      │          → 子任务入队 → 每个子任务走下方 N=1 确定性链
      │          → 父会话 fan-in（FR-13）            [全程 model.complete=0]
      └─ 否（N=1）：deterministic.try_compile(...)    [FR-2]
           ├─ None → 原 run() → CALL_MODEL（现状不变）
           └─ CompileResult
                → meter.check [FR-17]
                → S3 writer → source_ref [FR-10]
                → validate_and_test(完整签名，绕缓存) [FR-11]
                → BundleService.assemble → bundle_ref [FR-12]
                → state.terminate(DONE) [FR-14]
                → _finalize_process（共享）[FR-13/15]：
                   租约/_persist/事件 seq/task_turn/update_state/fan-in 或 write_back/ack
                → cost_sink(state) [FR-16]
```

### 7.2 接线四问
1. **谁调用**：`dispatcher.process_one` 两处分流（N>1 规划器替换、N=1 编译）；`_finalize_process` 由两路径共用。
2. **它调用谁**：`DeterministicPlanLLM`→`run_plan`/`PlanValidator`；compile→`S3EvidenceStore.put`/`validate_and_test`/`assemble`/`meter`/`cost_sink` — 全真端口。
3. **信号谁消费**：旁路不调 `decide()`（`decide` 只在 `run.py:275` 被调）；终态由 dispatcher 直接 `state.terminate` 构造；DONE 事件/artifact 事件由前端与总线消费；`bundle_ref` 由会话与 M09 消费；子任务 fan-in 由 `_fan_in_if_complete` 消费。
4. **闭环**：description → (plan) → spec → TSX → source_ref → 三门禁 → bundle_ref → 终态 → 租约/hash链/事件/账本/task/ack/fan-in，每条都有下游消费者。

### 7.3 装配入口
`worker.main()` 构造 `BundleService`（`confirm_secret` 等必填 fail-fast）、`S3EvidenceStore`、`DeterministicPlanLLM` 与 `TemplateRegistry`，注入 dispatcher；缺配置 fail-fast，禁止 None 降级。LLM 通道的 `GatewayPlanLLM` 保留，作回落与「开关关闭」时的默认。

### 7.4 模板登记表（完整场景覆盖，非简化首版）

按 ADR-004 §9 的 11 类场景逐一登记「可否确定性编译」与编译形态：

| 场景族 | 数据契约 | 确定性编译 | 说明 |
|---|---|---|---|
| 信息展示（时间/日期/日程文字） | `time` / `static` | ✅ 全量 | 时钟、日期、倒计时显示（静态骨架 + tick 数据） |
| 时间日程（闹钟/ reminder） | `time` / `local_storage` | ✅ 骨架 | 静态展示与本地设置项；设备侧真闹钟仍依赖 ADR-010 P-next |
| 效率工具（清单/备忘/便签） | `local_storage` | ✅ 全量 | 勾选状态的本地持久化骨架 |
| 健康运动（步数/心率展示） | `static` | ✅ 展示骨架 | 占位数据 + style；真实数据源属设备 API |
| 系统状态（电量/网速/流量） | `static` | ✅ 展示骨架 | 同上 |
| 设备控制（灯/空调/窗帘） | `static` | ⚠️ 仅静态占位 | 真实调用依赖 ADR-010 设备通道；不冒充控制能力 |
| 媒体娱乐（播放控制/歌词） | `static` | ⚠️ 仅静态占位 | 同上 |
| 生活服务（快递/外卖/公交） | `static` | ⚠️ 仅静态文案骨架 | 实时数据需外部 API，标 `static` 不得称实时 |
| 金融信息（股票/汇率/基金） | `static` | ⚠️ 仅静态文案骨架 | 同上 |
| 通讯社交（未读消息） | `static` | ⚠️ 仅静态文案骨架 | 同上 |
| AI 生成（每日摘要） | — | ❌ 回落 LLM | 摘要内容本质需模型推理 |
| 开放域问答/创作/设备直操 | — | ❌ 回落 LLM | ADR-004 负向语义族 |

> ⚠️ 场景的「静态骨架」是真实可渲染、可过沙箱的 TSX，但数据是占位/本地来源；必须在 spec 与 UI 上诚实标注，不得宣称实时或已接设备。

## §8 其他关键维度

- **数据与存储**：TSX 写 S3（key `code/{session}/{run}/widget.tsx`）；bundle/audit/cost 写 PG 既有表，无新迁移；模板登记表为代码常量 + 版本号，进 change_gate 指纹。
- **错误降级**：模板命中但门禁失败 → fail-closed 转 FAILED + error + audit，**不回落**（回落会把被拒内容换通道放行）；槽位不全/不可编译 → 回落 LLM。
- **安全**：模板产物与 LLM 产物走同一出护栏六判据；危险 API/PII 零容忍；模板不得拼接未转义用户输入（槽位填空需白名单字符过滤）。
- **兼容/迁移**：公共契约 schema 不改；事件 schema 不改（可观测走日志/指标）。
- **可观测**：日志/指标记录 generation_path（deterministic / llm-fallback / llm-native）、命中率、回落率、门禁失败率、模板版本。

## §9 样例之外覆盖

| 补全项 | 维度 | 来源 | 状态 |
|---|---|---|---|
| 日期/时钟静态卡片 | 同类枚举 | 规格示例 / ADR-004 A3（非用户原话） | 已纳入（FR-21 长尾边界约束） |
| 静态清单/备忘 | 同类枚举 | L1 样本库 | 已纳入 |
| 11 类场景全登记 | 同类枚举 | ADR-004 §9 | 已纳入 §7.4（含 ⚠️/❌ 边界） |
| 天气/股票实时数据 | 反向排除 | ADR-004 开放数据词 | 仅静态骨架，不称实时 |
| grid 未指定 | 边界值 | M06 SPEC_MISSING | **v1.2 修正**：先走澄清，用户答完用 clarified_grid 编译（FR-2）；答不出才回落 |
| 多 widget fan-out（N>1） | 同构场景 | ADR-006 | **确定性 PlanLLM 覆盖**（FR-3/5） |
| 带附件描述 | 同构场景 | `role_resolver.py:41` / `real_processors.py` | **v1.1 修正**：role 明确 + ref 存在 → 确定性编译（FR-19）；歧义才回落 |
| 两个 widget + 附件 | 复合复杂场景 | 用户本轮提问 | **v1.1 新增 FR-24**：附件事实注入 PlanningFacts → 分配子任务；归属歧义整单回落 |
| resume=1 / clarification_reply=1 | 同构场景 | `server.py:561-575` | 见 FR-20 |
| 预算触顶 | 异常路径 | `run.py:267` | FAILED BUDGET_EXHAUSTED（FR-17） |
| 并发重复提交 | 边界值 | `service.py:159` | 产物 hash 可复现，bundle 身份每次新（FR-12） |
| 模板产物门禁失败 | 异常路径 | M08 六判据 | fail-closed（FR-18） |

## §10 决策清单（原 U-1～U-14，用户授权 AI 拍板，全部已定）

| # | 原未决 | 本版决策 |
|---|---|---|
| U-1 | 首期模板集范围 | **全场景登记**（§7.4），非 3 类简化版；⚠️/❌ 明确边界 |
| U-2 | 分流开关 | env 开关 `WIDGETPILOT_DETERMINISTIC=on/off` + 模板白名单；默认 **on**（用户要完整功能），可一键关回落 LLM |
| U-3 | `generation_path` 落点 | 只写日志/指标，不改事件 schema（events.schema oneOf 漏判是独立议题） |
| U-4 | 门禁失败回落？ | **不回落**，fail-closed FAILED + error + audit |
| U-5 | M08 接通措辞 | **确定性通道新建完整生产链路**（N=1/N>1）；LLM 通道仍缺，不冒充全量 |
| U-6 | manifest 字段口径 | `model=deterministic-template:<registry_version>`、`prompt_version=<模板版本>`、`model_tier=cheap`、`retrieval_snapshot=template-registry@<hash>`（FR-9） |
| U-7 | 多 widget 规划 LLM | **零调用**：确定性 `PlanLLM` 覆盖 N>1（FR-3/5），成功标准含 N>1 |
| U-8 | resume/clarification | resume 不走；clarification grid 取 `clarified_grid or grid`（FR-20） |
| U-9 | 缓存与实耗 | 绕过 `_RESULT_CACHE` 真跑一次，记实耗（FR-11/16） |
| U-10 | 幂等/bundle_ref | 产物 hash 可复现（uuid5 widget_id）；`bundle_ref=bundle["bundle_id"]`；bundle 身份每次新 |
| U-11 | 模板版本化 | 版本号进 spec 指纹 + change_gate；新模板需 review（FR-22） |
| U-12 | 失败体验 | FAILED + reason_code + error + audit；不新增 guard_hit 写入点 |
| U-13 | 终态事件重复 | 复用 assemble 的 artifact 事件；DONE 只标终态，载荷不含 bundle 详情 |
| U-14 | 长尾 + 显式 grid | 规整说法才命中；长尾仍走 L2/L3；route_layer 统计不变（FR-21） |
| U-15（v1.1 新增，AI 自裁） | 附件能否走确定性通道 | **能**：附件处理（role 规则、OCR、主色）全零 LLM；仅 role 歧义/无 ref/能力冲突回落（FR-19） |
| U-16（v1.1 新增，AI 自裁） | 多 widget + 附件如何分配 | 注入 AttachmentFact 到 PlanningFacts，按子任务描述归属；归属歧义整单回落（FR-24） |
| U-17（v1.2 新增，AI 自裁） | grid 没写就回落是不是太薄 | **不回落**：复用 SPEC_MISSING→AWAITING_INPUT→clarified_grid 现成闭环，用户答完再编译（FR-2） |
| U-18（v1.2 新增，AI 自裁） | 长尾/口语描述要不要拒 | **不拒**：L2/L3 出 WIDGET_GENERATION + data_contract 可确定 + grid 明确即可编译，靠 FR-25 双锚定而非改 L1 词表 |

## §11 约束与红线

- 不破坏既有公共契约；`Deps.model` 必填与 `run()` `CALL_MODEL` 不动。
- 模板产物必须过 M08 三门禁，禁止豁免；⚠️ 静态骨架必须诚实标注数据来源。
- DONE 必带真实 `bundle_ref`（`types.py:386-391`）；N>1 必须真 fan-in。
- 不写 fake/stub 冒充 adapter；真实环境用 podman；回落路径必须真调原端口。
- 「零 LLM 调用」由 mock gateway 计数证明，不由日志标签推断。

## §12 验收标准

| 要求 | 验证方式 | 达标口径 |
|---|---|---|
| FR-1/2/4/6/21 | 单测：命中/未命中/歧义/长尾 + mock gateway 计数 | 命中 0，回落原样，硬样本绿 |
| FR-3/5 | 多 widget 规划单测（N=2/3 + 不可编译回落） | gateway=0 / 回落>0 |
| FR-7/8/9 | schema + 双次 compile canonical_json + check_contract | 全通过 |
| FR-10/11 | 真 podman 沙箱 + 绕缓存用例 | passed，缓存不短路 |
| FR-12/13/14/15 | PG 查 bundle + 会话终态 + fan-in + ack/seq | 真产物、无重投、父会话 COMPLETED |
| FR-16/17 | cost_ledger 行 + 预算触顶用例 | sandbox_minutes>0 / tokens=0 / 触顶不进沙箱 |
| FR-18/19/20/22/23 | 失败/附件/澄清/版本/日志断言 | 留证齐全、统计不变、指纹触发 |

## §13 变更记录

| 变更点 | 原结论 | 新结论 | 理由 |
|---|---|---|---|
| 生成路径 | 只有 LLM 一条 | 确定性（N=1+N>1）+ LLM 双通道并行 | 用户核心诉求 |
| 范围 | v0.1 草稿「首期 3 类静态模板 + 仅 N=1」 | v1.0 全场景登记 + 确定性 PlanLLM 覆盖 N>1 | 用户明确「不做简单化、不因首版做简单版本」 |
| 红队 Blocker | 10 项未闭合 | FR-6/8/9/10/11/12/13~23 全部回填 | 模式切换复审 |

## §14 下游交接

- 决策清单已全部拍板 → 可进 change-contract 固化与 S3 TDD。
- 同步：ADR-013（设计权威）、`00-总览` ADR 索引、M03/M04/M06/M08/M09 覆盖声明、M09 模型缺口文档重划线。
- 计划头 `Spec:` 指向本文件。
