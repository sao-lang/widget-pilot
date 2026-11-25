# 确定性 widget 生成通道 · 六阶段计划进度（单一事实源）

> 状态日期：2026-11-25 19:30。本文是 P0–P6 进度与缺口的**唯一权威口径**，取代散落在 overview / 各契约 Open 段 / ADR-013 §8 的零散描述。
> 范围来源：六阶段划分归纳自 P1/P2 契约 Open 段、主 overview S1 声明与 ADR-013 §9；若与早期口头表述不一致，以本文为准并同步回写契约/ADR。

## 0. 口径纠正（最重要，先读）

**"过 conductor 门禁" ≠ "功能做完"。**

P0/P1/P2 每阶段都通过了测试全绿、review Blocker=0。但门禁放行的口径是「本轮改动的局部正确性、无造假绿」，**不是「端到端链路可用」**。大量缺口是以"诚实边界 / Open 项 / 后续项"登记后才放行的。这些被放行的断点累积后，导致：

- ADR-013 §8 的定性仍成立：「从描述到真 bundle 的端到端链路当前不能用」。
- 生产链路里存在多处"定义有、测试过、无生产消费者"的死代码（见 P2）。

因此**不能仅凭"P0–P2 已过门禁"就推进 P3**：P3（本地权重+命中率实测）依赖完整链路可用，而当前链路断着；补权重也解决不了接线缺口。

阶段状态用三态标记：
- ✅ **做完**：功能在生产链路可用，且有测试覆盖。
- ⚠️ **做了没接通**：代码/测试/契约齐全，但生产无消费者或链路断点（假完成，最需警惕）。
- 📋 **范围外/未开始**：本阶段计划里就没包含，或明确排到后续阶段。

---

## P0 · 确定性通道纵向打通

**范围**：成分解析 → 选配方 → 填槽位 → WidgetSpec → TSX → 真三门禁 → 真成包，零云端 LLM 的最小闭环。

### ✅ 做完
- 成分→WidgetSpec→TSX 编译器：`services/deterministic/compiler.py`，真生成白名单 TSX（非脚本字符串）。
- 解释器规则层：`services/guardrails/interpreter/rule.py`，共现消歧/分散槽位聚合/后置 grid 分配；工具链规划 `capability_planner.py` 覆盖 33 能力。
- 四分支分流：`services/deterministic/channel.py`，compiled/rejected/clarification/llm_fallback。
- 真三门禁：`channel.py:132-145` 调 `validate_and_test`（输出护栏 + manifest 契约 + 沙箱）；真成包走 `BundleService.assemble`。
- 真环境验证：podman 真沙箱集成测试通过（`tests/integration/test_v3_deterministic_podman.py`）。

- **（2026-11-25 21:15 接通，独立复审 Blocker=0）N>1 多 widget**：`channel.py` compile_one helper 循环所有 `result.instances`，各走完整三门禁+成包；`ChannelResult` 加 `bundles`/`elapsed_ms`，`bundle` 保留首实例兼容 dispatcher 单值 `session.bundle_ref`。实测 2 实例产 2 个不同 bundle_id、source_ref 不撞、都可在 store 按 session 查到；第 2 个门禁失败整体 failed 不留半成品。
- **（2026-11-25 21:15 接通）附件生成侧**：`deterministic_runner.py` 注入真 `load_fn=invoke.load` + `attachment_refs.py` 把 id 解析为带 hash source_ref 的富对象（修 `Input.attachments: list[str]` 类型断点）；`compiler.embed_assets` 读派生图 ref 经 magic bytes 校验 + 64KB 上限转 base64 data URL（沙箱 --network none 唯一可出像素形态），`render_tsx` 白名单+json.dumps 安全输出 `<img>`；契约 `widget_spec.schema.json` 加 `image_data_url` optional + 四同步。恶意脚本/超大图/非图片自动降级纯文本。
- **（2026-11-25 21:15 部分接通）四类返工**：②ambiguity 重解析已由追问闭环覆盖（P2）；④失败成本账数值真实化——`ValidateResult.elapsed_ms` 回灌 `State.budget_used.sandbox_minutes` → `CostEntry`（实测 30000ms→0.5）；①确定性修复表五条经逐一核实大多在确定性路径**不可达**（compiler 白名单/同源保证），硬做是修不存在的 bug，不造；③有界 LLM 返工依赖真模型端点（M07 缺口），`llm-rework` 值域预留但零生产点，不假绿。顺手修 `entry.py` 只缓存 passed（失败每次真跑，防瞬时故障被永久缓存复用）。

### ⚠️ 剩余（依赖模型/权重，排 P3）
- **本地小模型层形同虚设**：`interpreter/weights.py` 只校验权重文件存在 + sha256，无 onnxruntime 真推理；`local_model.py` 缺权重即 `fallback`。生产只注入 `RuleInterpreter`，`LocalModelInterpreter` 从未实例化 → "三层解释器"生产只有规则层（排 P3，依赖真权重）。
- 返工③ `llm-rework` 实际产生路径、`WIDGETPILOT_REWORK_LLM` env 读取：依赖 M07 真模型端点，当前 off 是唯一态（诚实缺口，非假绿）。

### 📋 范围外
- 多配方：仅 2 个 recipe，`compiler.py:53` 硬编码 `recipe.task.list.v2`（排 P6）。
- 描述压缩降级：`router.py:22-41` 只有规则→本地→云端，无压缩环节（排 P5）。
- 真权重 / 视觉 / LLM 端点（排 P3）。

---

## P1 · 生产 dispatcher 分流

**范围**：把 P0 通道接进生产，validation.ok 后带开关分流，命中走三门禁+成包，fallback 继续 LLM。

### ✅ 做完
- `Dispatcher` 三参钩子 `DeterministicRunnerFn` + `_try_deterministic` 四分支分流（`dispatcher.py:440-488`）。
- 生产工厂 `deterministic_runner.py`：BundleService / S3EvidenceStore / DockerCliRunner 装配，缺 S3/JWT fail-open。
- `worker.py` 装配钩子；`WIDGETPILOT_DETERMINISTIC` 开关（off 零行为变化）。
- 命中分支履行 process_one 全部后置义务（ack / task_store / fan_in / write_back），带 hash 的 source_ref 传门禁（修过 S3 端口 B2）。
- **（2026-11-25 19:40 修复）子任务澄清挂起不再误判为失败**：`dispatcher.py:896-920` 把 `terminal=None + phase=AWAITING_INPUT` 落 `TaskState.AWAITING_INPUT`（而非 FAILED）；`is_terminal` 不含该态 → `fan_in` 返回 `terminal=False`，会话不被裁决。回归测试 `tests/unit/test_v3_child_clarification_state.py`（2 用例）。
- **（2026-11-25 19:55 修复）命中分支落成本账**：`dispatcher.py:885-897` 在命中确定性分支且 `state.terminal is not None` 时调 `self._cost_sink(state)`（仅终态落账，clarification 挂起不落；llm_fallback 由 run() 内部落账不重复）。回归测试 `test_v3_deterministic_wiring.py` 2 用例。
- **（2026-11-25 20:25 修复）reason_code 枚举归一**：`dispatcher.py:137-152` 新增 `_normalize_reason(raw, branch)`——通道自由串 `"GATE_FAILED"` 等归一到 `ReasonCode`（rejected→INTENT_OUT_OF_SCOPE、其余→GUARD_OUTPUT_HIT），已知值原样返回；FAILED 会话落库 reason_code 恒为枚举值。回归测试 2 用例。

- **（2026-11-25 20:15 修复）命中分支不发终态事件**：原状态 compiled 命中发 0 个事件（fallback 走 run() 发 5 个），前端 SSE 收不到完成通知。`Dispatcher` 新增可注入 `terminal_event_emitter`（`:364/:401`），命中分支在 write_back 后发射（仅非子任务，子任务由 fan_in 统一发）；生产 worker 注入 `dispatcher_events.make_terminal_event_emitter(deps)`——seq 与 run.py 同源、payload 一致，DONE/ERROR/ASK_USER 按 state 分派。回归测试 `test_v3_dispatcher_events.py`（4 用例）+ wiring 2 用例。
- **（2026-11-25 20:35 修复，ADR-019）LLM 路径产物校验 + bundle_ref 口径**：深查后撤回原文"下游按 ref 解析失败"的错误假设——`session.bundle_ref` 生产仅 1 读取点（原样透传 REST），M09 全端点按裸 bundle_id 反查，带 hash 反而 403；四条状态链同源同值口径天然一致。正解=确立裸 id 单一口径 + `Dispatcher.artifact_verifier` 校验 DONE 的 bundle 真实存在且属主（worker 用 `PgBundleStore.get` 注入）——M09 成包以三门禁全过为前提，故存在即已过门禁，不必重跑 `validate_and_test`（LLM 终态本就无 spec/source/manifest 三入参）。契约 `bundle_refs[]` 空壳改为 `bundle_ref` 单值 + 四同步（drift=[]）。回归测试 2 用例。
- **（2026-11-25 20:35 修复，ADR-019）generation_path 可观测**：迁移 0020 给 `cost_ledger` 加 `generation_path` 列（server_default=llm-only）+ 建 `generation_occurrence` 表（全分支漏斗）。`CostEntry`/`CostEntryMeta`/`accrue`/`pg_ledger`/`models.py` 同步加字段；`request_context.py` 用 ContextVar 承载 path（不改 run() 的 cost_sink public 签名）；`_try_deterministic` 各分支埋点（path+occurrence），worker 注入 PG occurrence sink。值域 deterministic/cloud-fallback/llm-only/llm-rework。回归测试 `test_v3_generation_path_cost.py`（2）+ wiring 2 + PG 集成 3 真跑。

### 断点状态
- 纯软件断点已清零：P1 全部接通（历经 #35/#37/#42/#38 + ADR-019 三项）。

### 📋 范围外
- 默认开关为 on（灰度风险， Nit 项）；`llm-rework` 值预留但 P5 返工代码未实现；ADR-012 历史版本恢复（独立缺口）。

---

## P2 · 会话注册表与修改定位

**范围**：会话内 widget 可查、修改意图能定位目标、定位不明反问带精准信息。

### ✅ 做完
- `list_by_session`：`BundleStorePort` + `PgBundleStore` + `InMemoryBundleStore` + `BundleService` 四处实现（租户隔离、created_at 降序，复用 0019 索引，无新迁移）。
- target 定位器：`bundles/registry.py` 五方式 + 不明返回 `{"choices": 精准信息}`（bundle_id/grid/host/data/created_at）。
- 意图词族映射：`rule._intent_category()` 四类（取消>继续>修改>生成）。
- 能力定义层：`tool.session.*` 三个能力（L0-L1-L2，args 不含 user_id 防越权）。
- `IntentCategory` 扩 MODIFY/CONTINUE/CANCEL。

- **（2026-11-25 20:50 接通，两轮独立复审 Blocker 清零）修改定位三件套**：
  - `request_context.py` 四 ContextVar（path/user/session/clarification_payload），`process_one` 入口 set + finally reset（修了前轮 path 裸 set 泄漏）；`tool.session.*` 从 ContextVar 读 user_id，args 无该字段、无上下文 fail-closed；`deps.py` 注入生产 `BundleSessionQuery`（走 store 强制 user_id 过滤）。
  - `_intent_category` 写入 `SlotFill.intent_id`（rule + local_model）；修改正向门放宽但要求显式控件产物名词（防"取消订单"误编译）；`_LOCAL_DATA` 补日历/任务/日程（修真实解释器 data 空）。
  - `channel.py` 修改意图先 `resolve_target` 定位（target_hint 支持中文序数/回答覆盖）；定位不明→反问带精准 5 字段，落 `SessionRow.evidence_ref` + SSE ASK_USER payload（question_kind/choices/evidence_ref）。
  - **fail-closed（复审 B1）**：定位到目标但重成包未实现前，返回 `clarification(target_unsupported)`，不再 compile+assemble 造无关新包（杜绝"假绿修改成功"）。

### 📋 范围外 / 未开始
- **基于目标 bundle 的重新成包新版本**（P5 返工）：`assemble` 永远新 uuid、`append_version` 入口空链、ADR-012 版本切换写回未做——本轮刻意 fail-closed 而非假实现。
- target 定位是文本匹配，非真语义理解；REST 端点未单独加（能力层已可用）。

---

## P3 · 本地小模型权重 + 命中率实测（未开始）

- 真 BIO 序列标注权重（SNIPS 路线）+ `from_env` 路径；当前 `weights.py` 只有校验壳。
- `LocalModelInterpreter` 生产实例化并注入 `InterpreterRouter`。
- 命中率实测——**前置：P0–P2 的接线断点必须先补，否则测出的命中率无意义**。

## P4 · 附件全链路（部分完成 2026-11-25 21:15）

- ✅ 派生 ref 写进 TSX：`<img src={dataURL}>` 内联（沙箱无网络唯一形态），白名单+magic 校验+64KB 上限。
- ✅ 生产传 `load_fn=invoke.load`，附件 id→富对象，能力工具链真执行（frame_at/csv.filter 等）。
- ✅ U-5 文本回灌接线已做（`run.py:_append_capability_feedback_message`）；图片语义消费依赖多模态端点（排 P3）。
- ✅ **U-8 附件洞察槽注入 M07（2026-11-25 21:50 完成）**——`AttachmentInsight` 的生产数据（CSV 统计/视频帧/本地 OCR/图片主色，零模型）已真实落 `insight.json` + PG `insight_ref`；现由 `services/orchestrator/memory/attachment_adapter.py:StoreInsightPort` 按 id 读 insight JSON（hash 校验 + 超预算截断留 key），`worker.py:_enrich_input` 注入 `attachment_port`。此前把它整体标为"依赖真视觉端点"是不实声称，与 U-5 混为一谈了。

## P5 · 返工 + 压缩降级 + 修改闭环（部分完成）

- ✅ **重成包新版本（2026-11-25 21:50 完成）**。此前声称"依赖 M07 模型回路/真实模型端点，无新 TSX 产物来源"是**错的**。实证：`RuleInterpreter` 对"把第二个日历改成 4x4"已产出 `grid=4x4/data=日历/host=日历`（零模型），`compile_spec` 只需这 4 槽；目标 bundle 旧 spec 形状与编译输出逐字一致可继承；`versions.append_version` 支持追加、`PgBundleStore.replace_versions` 支持复用 bundle_id（rollback 已是生产范例）；ADR-012 只缺"会话指针回写"，版本链追加本身可做。现已实现 `BundleService.rework()` + `PgBundleStore.update_content()` + `merge_rework_slots()` 继承 helper：复用 bundle_id、追加版本链，全程零 `model.complete`。此前 fail-closed 避免假绿是对的，但"依赖模型"定性错误。
- ✅ 四类返工：②ambiguity 追问闭环已覆盖、④成本账数值真实化；①修复表大多不可达不硬做。
- ✅ **返工③有界 LLM 返工（2026-11-25 21:50 完成接线，真调用留后续）**：拆分表见 `docs/模块-模型依赖与接线拆分表.md`。已落 `services/deterministic/rework.py`（零模型调度：失败证据回填、最多 1 次、非法输出拒绝）+ `services/orchestrator/rework/model_ports.py`（`ReworkLLMPort` 协议 + `GatewayReworkLLM` 真实接线点，走 `deps.model`）+ `channel.py` 返工分支（同款三门禁不豁免、`generation_path=llm-rework`、`model_calls=1`）+ `WIDGETPILOT_REWORK_LLM` env 门控（off 零调用 / on 缺 gateway fail-fast）+ 7 个 mock 测试。真实 `model.complete` 统一留后续接线；mock 只证明调度不变量，不代表模型能力已验收。
- ✅ P1 遗留已全修（澄清误判 / cost_sink / generation_path 指标）。
- 📋 描述压缩环节：核实后判定**设计上不该做**（ADR-009 N3 禁止 LLM 摘要/压缩，规则解释器已直接抽 grid/host/data 无需压缩；真正的 tool-message 窗口压缩 `budget.py` 已实现）。此前"排 P5"是伪需求。

## P6 · 多配方 + N>1（均已完成）

- ✅ N>1：`channel.py` 循环所有 instances，N 个 bundle 全成包入库（2026-11-25 21:15）。
- ✅ **多配方复用链路（2026-11-25 21:50 完成）**：`_FEATURE_RENDER` 每项加 recipe_id，`compile_spec`/`build_manifest` 按 data 特征推导（显式参数优先），生产无需传参即生效；补 `recipe.weather.card.v1` 文件 + 注册。storage_namespace 是自由 string，未改契约。
- 📋 确定性 PlanLLM：ADR-013 `:72,117` 与契约承诺过，但**代码从未实现**（`services/deterministic/` 只有 3 文件）；N>1 实际由 `rule.py:224` 规则计数完成。广义规划语义仍由用户决策维持真模型（fail-fast）；窄义包装纯适配，非功能突破。
- 📋 PlanValidator：核心版已纯软件实现（`planner.py:89-143`），但 ADR-006 §6.7 的附件 role 兼容/`check_input`/预算切分未覆盖（缩减版）。
- 📋 fanout N 判定同源化：仅优化——`needs_planning` 只触发规划器不决定 N，P0 后 N>1 不丢数据；误判方向是"多走模型"非"丢数据"。

---

## 跨阶段共性缺口

| 缺口 | 证据 | 影响 | 状态 |
|---|---|---|---|
| ~~LLM 路径不过三门禁~~ | ADR-019 artifact_verifier | 成包即已过门禁，存在性校验闭合 | ✅ 20:35 |
| ~~命中不落成本账~~ | `dispatcher.py:920-924` | 命中终态落账 | ✅ 19:55 |
| ~~generation_path 无持久化~~ | 迁移 0020 + contextvar | cost_ledger 加列 + occurrence 漏斗 | ✅ 20:35 |
| ~~bundle_ref 丢 hash~~ | 深查后撤回假设 | 口径天然一致，存在性校验 + 契约单值 | ✅ 20:35 |
| ~~per-request 用户上下文缺失~~ | request_context.py 四 ContextVar + finally reset | P2 三件套已接通（重成包仍留 P5） | ✅ 20:50 |
| ADR-013 §8 已过期条目 | 文档称"无 TSX 生成器"，实际 `compiler.py` 已实现 | 文档与代码不一致，需回写 | ⚠️ 文档 |
| ~~重成包新版本（P2/P5）~~ | `BundleService.rework` + `update_content` + slot 继承，版本链追加 | "改 widget"闭环可用 | ✅ 21:50 |
| ~~U-8 附件洞察槽注入 M07（P4）~~ | `StoreInsightPort` 生产适配器 + worker 接线 | 模型首轮可见附件内容 | ✅ 21:50 |
| ~~多配方复用（P6）~~ | `_FEATURE_RENDER` 加 recipe_id，按 feature 推导；补 weather recipe | 配方系统对产物生效 | ✅ 21:50 |
| PlanValidator 缩减版（P6） | `planner.py:89-143` 缺 ADR-006 §6.7 四项检查 | 幻觉/越界产物风险 | 📋 纯软件可做 |

## 推进原则（重新清理的依据）

1. **先补接线断点，再谈模型权重**。P0–P2 的 ⚠️ 项是纯软件缺口，不依赖模型，且阻塞 P3 的命中率实测。**注意（21:35 纠正 / 21:50 收敛）：P4 的 U-8、P5 的重成包、P6 的多配方复用曾是被误判为"依赖模型"的纯软件缺口，本轮已全部补齐；但教训保留——阶段通过 ≠ 功能完成，禁止用"所有不依赖模型的链路都通了"这种概括收尾。**
2. **每阶段收尾必须区分"过门禁"与"生产可用"**：定义有消费者才算做完，死代码必须标 ⚠️ 并登记接入口。
3. **文档与代码同等交付**：本文状态变更时，同步回写 ADR-013 §8、对应契约 Open 段、主 overview 进度索引。
