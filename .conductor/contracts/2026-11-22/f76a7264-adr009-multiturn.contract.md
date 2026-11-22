# Change: ADR-009 多轮对话独立部分落地 / 2026-11-22 / confirmed

## Why
用户否定"单任务一次性"定位：真实场景是一个会话内对同一 widget 反复迭代修改。ADR-009 草案拷问发现完整设计依赖未落码的 ADR-006（`generation_task`/`task_id`），用户拍板：未决项按推荐处理 + 本轮走 A 方案（只落不依赖 ADR-006 的独立部分）。原拍板含"产物复用 `SessionRow.bundle_ref` 覆盖"，实现核查发现 `SessionRow` 无 `bundle_ref` 列（`SessionService.complete()` 接收但不落库，既有 B4 缺口，见 M04 指南:326）——该子项**本轮无法交付**，已在 Open 段登记，不写虚假 Invariant。目标是在当前单任务链路上实现"会话完成后可再提交修改、历史事实进上下文"，不建新表、不碰未落地的多任务模型。

## Delta
- 会话 `submit_message` 放开前置状态：`DRAFT | COMPLETED` 可提交新控件消息；`RUNNING/AWAITING_INPUT/VALIDATING` 仍拒绝并发提交；新一轮分配新 `run_id` 写回现有 `Session.run_id`（parent_run_id 语义）。
- 新增 `services/orchestrator/memory/fact_summary.py`：从同会话历史轮次纯代码抽取 `FactSummary`（零 LLM），字段白名单（widget_type/grid/palette/key_features/last_intent/turn_count），上限按 O1（任务 8 / 特征 5）；PII/注入过滤后文本（沿用 M06 已过滤的输入）。
- FactSummary 持久化：新增 PG 表 `fact_summary`（`session_id` + `turn_index` + `payload` JSONB + `created_at`），迁移 `0014_fact_summary`；按 `(session_id, turn_index)` 唯一，advisory lock 串行化写入，PG 失败 fail-closed（延续 M07 存储纪律）。
- 事实生成与落库时机：`dispatcher._write_back` 终态（DONE/FAILED 有产物时）同步生成并落库 FactSummary；失败 turn 不生成新事实（保留上一版）。
- 结论槽（第 6 槽 `slots.py::_conclusion_message`）尾部追加当前会话最新 FactSummary 渲染段；1–5 槽字节不变（caching 前缀稳定，I18）；`SlotConfig` 增 `fact_summary: str | None` 注入点。
- `Input` 增 `fact_summary` 渲染文本字段（装配读它，不改 `run()` 装配逻辑）；dispatcher 构造 Input 时直接读 `fact_summary_store.latest(session_id)` 并注入，读失败容错为空串。
- 保留字段：REST `POST /messages` 请求体增 `target_task_id?`（本轮单任务模式忽略，仅占位以便 ADR-006 落地后接；须在契约 Open 段登记退出条件）。
- 文档：ADR-009 v0.2（拷问+范围收窄）、00 总览 ADR 索引状态、M04 状态语义注记、M07 结论槽注记。

## Invariants
- I-M2/零 LLM：`FactSummary` 生成全程 `call_count == 0`；纯代码从 `State.spec` + 已过滤输入抽取，禁调用模型。
- I-M1/白名单：FactSummary 只存短字段枚举/短字符串，禁 PII 原文、附件原文、历史代码；`last_intent` 必须是已过 M06 注入/PII 过滤的输入。
- 非目标边界：多轮只针对控件生成/修改意图；非控件意图由 M06 级联照常拒绝，多轮路径不绕过门禁（ADR-009 §1 覆盖声明）。
- caching 前缀：结论槽 FactSummary 只在最后一段追加，1–5 槽字节 diff = 空（I18 扩展为跨轮前缀断言）。
- fail-closed：FactSummary 表 PG 写失败抛异常（不降级内存、不静默丢事实）；读取失败时装配省略该段（事实是建议非门禁，读取侧容错）。
- 单任务模式：本轮无多任务表、无 turn 级版本回退；不承诺产物版本覆盖（`SessionRow` 无 `bundle_ref` 列，既有 B4 缺口，见 Open 段）；Fail 时保留上一版 FactSummary。
- 最小变更：不建 `generation_task`/`task_turn`（ADR-006 未落码）；`target_task_id` 仅为保留字段，禁在单任务模式消费它。

## Affected
- `services/control_plane/sessions/service.py`（submit_message 状态前置）
- `services/orchestrator/memory/fact_summary.py`（新增）+ `services/orchestrator/memory/ports.py`（FactSummaryStorePort）
- `services/orchestrator/dispatcher.py`（_write_back 生成落库 + Input.session_id 传递）
- `services/control_plane/api/server.py`（target_task_id 保留字段）
- `packages/agent_core/context/slots.py`（结论槽追加）+ `packages/agent_core/types.py`（SlotConfig.fact_summary）
- `services/control_plane/migrations/versions/0014_fact_summary.py`（新增）
- `services/control_plane/models_fact_summary.py`（FactSummaryRow，独立文件避免与并行改造的 models.py 冲突；复用同一 Base）
- 文档：ADR-009 v0.2、00 总览、M04、M07（已部分同步，本次核对）
- 测试：`tests/unit/test_m07_fact_summary.py`（新增）、相关 dispatcher/slots 测试增量

## Acceptance
- `.venv/Scripts/python.exe -m pytest -q` 退出码 0（真 PG 集成真跑时；Docker 未起则集成 skip 属环境非回归，但 FactSummary 单测必须真跑通过）；新增用例覆盖：FactSummary 零 LLM、白名单过滤、跨轮前缀 diff=空、COMPLETED 可再提交、失败 turn 不生成新事实。
- `ruff check .`、`npx pyright --pythonpath .venv/Scripts/python.exe`、`pnpm contracts:check` drift=[]、`contract_check.py`、`commitment_ledger.py --strict`、`source_purity.py --strict`、`review_gate.py` 全退出 0；新增代码零 type ignore。
- 真 PG 集成（Docker 起时）：`fact_summary` 表按 session+turn 唯一、advisory lock 并发写不丢版本、写失败 fail-closed；本机 Docker 不可用则登记"待真实环境验证"未完成项，不砍功能不写 fake。
- ADR-009 §12 的 IN 项逐条有代码证据与测试；OUT 项未误实现（grep 无 generation_task/task_turn 新建）；设计文档与代码双向一致。

## Open
- **产物版本覆盖未交付（用户原拍板的 A 方案子项）**：`SessionRow` 无 `bundle_ref` 列，`SessionService.complete()` 接收但不落库（既有 B4 缺口，M04 指南:326 / M09:1054-1056）。需先补 B4（会话产物引用落库）才能实现"覆盖当前版本 + 版本回退"；在此之前多轮的事实链可用，但产物版本链不可用。
- `target_task_id` 保留字段：ADR-006 落地（`generation_task` 表 + task_id 标识）后必须消费或删除，禁止永久占位；退出条件写入 ADR-009 §12 OUT 清单。
- ADR-009 完整版（多任务并存、task_turn 版本回退、按 turn 成本分账）硬前置 ADR-006 P0–P2，本轮明确不做。
- 事实摘要上限 8/5 为影子校准初值，M10/M11 评测后回填；非本轮阻塞。
- 模型端点缺失（无真实 LLM）不影响本轮（FactSummary 零 LLM）；L4/分档/Responses 缺口仍按 M07 模型缺口文档追踪。
