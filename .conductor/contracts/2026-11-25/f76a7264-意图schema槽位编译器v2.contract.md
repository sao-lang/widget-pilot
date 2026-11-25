# Change: f76a7264-intent-schema-compiler-v2 / 2026-11-25 / confirmed

## Why
用户明确这是专用手机 widget 生成 agent，输入含文字 + 四类附件（视频/图片/CSV/文本），一次可生成多个 widget；期望复杂描述精准命中零 LLM 路径直接生成，并直言此前设计「太薄」——`桌面上加个 2x2 显示今天日期的卡片` 太简单。调研 Google A2UI、Apple App Schemas、Snips NLU、企业文档抽取共识后确认：大厂不做纯规则读懂任意复杂描述，而是让语言理解停在结构化中间层、执行层刚性；本仓库缺的正是「意图 schema + 槽位抽取 + 归一化」这层，不是词表不够大。用户拍板：纯规则 + 完整 schema + 附件解析（严格零 LLM），重写设计到 v2.0 并出 ADR-015。

## Delta
- 新增 `services/guardrails/intent_schema/`：`IntentSchema`/`SlotSpec` 声明式常量（11 族对齐 ADR-004 §9）、`compile_intent(description, attachments, fields)`、`SlotFill`/`NeedClarification`、`实体归一化`、`role × kind → 处理器 → slot` 映射矩阵。
- 新增理解层前置：`validate_chain.py` 在进护栏后、语义层前调用 compile_intent；**删除 step11 的 `classify_intent` 调用**，理解权归编译层唯一所有。
- 替换三处分散澄清为统一 `NeedClarification(missing_slots, question_kind, dialog)`；`role` 歧义保留 `_ROLE_HINT` 专属文案（`question_kind=role`）。
- 内部 Python 类型增字段（非公共 JSON Schema）：`ValidationResult.slot_fill`、`SemanticResult.slot_fill`、`PlanningFacts.slot_fills`、`Input.slot_fills`。
- 新增 `RouteLayer.COMPILER` 枚举值（覆盖 ADR-004 §11 红线，硬样本层仍记 L1/L2/L3）。
- 新增 `DeterministicPlanLLM`（实现 `PlanLLM` Protocol，显式注入非静默降级）+ SlotFill 按 `(session_id, run_id, instance_id)` 持久化（多实例独立追问、幂等不重复生成）。
- 新增 ADR-015 作为设计权威；ADR-013 理解层部分被覆盖，门禁/成包/终态成果保留。
- 本主题 v1 契约（`f76a7264-确定性widget生成通道.contract.md`）作为历史保留；本契约为 v2 补充设计，冲突处以本契约与 ADR-015 为准。无删除公共符号。

## Invariants
- 公共 JSON Schema 不得改：`widget_spec.schema.json`、`widget_bundle.schema.json`、`events.schema.json`、`IntentCategory`/`WidgetType` 枚举、SDK 四同步产物。
- `run.py`/`decide.py`/`Deps.model` 的模型分支不得改；`GatewayPlanLLM` 语义不得改。
- 严格零 LLM：理解层与生产装配不得注入 embedder/classifier/gateway；`validate_chain` 的这三个参数删除。
- 安全护栏（注入/PII）必须在理解层之前；编译输出按不可信数据处理。
- 拒绝类零模型（I14）：L1 仍最前短路；ADR-004 三条硬样本与去类型化内核不变。
- DONE 必带真实 `bundle_ref`，过同款 M08 三门禁与 finalizer（ADR-013 成果）。
- 编译能力不足（schema 未覆盖/自由表达）：**降级到现有 LLM 通道**（`generation_path=llm-fallback`），理解层本身仍零 LLM；可补槽位仍先追问；降级产物门禁失败 fail-closed，禁二次降级。
- 不写 fake/stub 冒充 adapter；真实环境用 podman。

## Affected
- 代码模块：`services/orchestrator/validate_chain.py`（删 step11 + 三端口 + 插入编译层）、`services/guardrails/semantic.py`（消费 SlotFill、feature 入编译层）、`services/guardrails/intent_schema/`（新增）、`services/guardrails/intent_cascade.py`（L2/L3/L4 注入删除，保留 L1）、`packages/agent_core/types.py`（ValidationResult/Input/SemanticResult 增字段 + RouteLayer.COMPILER）、`services/orchestrator/tasks/ports.py`（PlanningFacts 增 slot_fills）、`services/orchestrator/tasks/deterministic_plan_llm.py`（新增）、`services/orchestrator/worker.py`（装配理解层，禁模型注入）、`services/control_plane/attachments/real_processors.py`（复用，不改）。
- 设计文档（S5 同步核对清单）：`docs/adr/ADR-015-意图Schema驱动的槽位编译器.md`（新增，权威）、`docs/adr/ADR-013`（理解层部分标注被覆盖）、`docs/adr/ADR-004`（RouteLayer 覆盖声明回写）、`docs/modules/00-总览与模块地图.md`（ADR 索引 + 15）、`docs/modules/M06-输入校验护栏与路由域.md`（校验链顺序 + 统一澄清）、`docs/modules/M05-附件域.md`（附件作 slot 源）、`docs/modules/M08`/`M09`（理解层到门禁的接线与缺口重划线）。
- 影响面（impact_analysis 需 S3 前补跑）：`classify_intent`/`validate_semantic`/`resolve_role`/`process_data_source` 的调用点。

## Acceptance
- `coverage_check.py --matrix .conductor/contracts/2026-11-25/f76a7264-意图schema槽位编译器v2.coverage.md --spec .../v2.spec.md` 退出 0（本轮实测 34 行 PASS）。
- `contract_check.py --contract <本文件>` 退出 0（六段齐全 + Acceptance 非空）。
- FR-1/2：11 族 schema 自校验通过；改 schema 后 change_gate diff_config 非空。
- FR-3/4/5：≥20 条复杂描述样例集，双编译逐字段一致，槽位锚定不裸放；一次或一次追问成功率 ≥70%。
- FR-6：视频 `content_extraction` 走逐帧 regions 填 text slot；空 CSV（row_count=0）进 missing；gateway 计数 0。
- FR-7：2 widget + 2 附件、一个缺槽位 → 补答后只生成缺失实例，另一 bundle 不重复落库。
- FR-8/9/10：顺序断言（护栏→编译→语义）；grep `classify_intent(` 无生产调用；语义层无 `_has_required_slots`/`_detect_feature` 重复抽取；role 追问文案不丢。
- FR-11/12：SlotFill 可从编译追到 spec 构造器；`validate_chain` 无 embedder/classifier/gateway 参数；ADR-004 三条硬样本原样绿。
- FR-13：编译归因（intent_id/槽位/缺失/附件质量/schema 版本）日志可查。
- S3 后置（依赖 ADR-013）：真 podman 三门禁 + PG bundle + 终态 + gateway 计数 0。
- `npx pyright --pythonpath .venv/Scripts/python.exe` 0 errors；`.venv/Scripts/python.exe -m ruff check .` 全绿；`pnpm contracts:check` drift=[]；写码后 `source_purity --strict` 与 `review_gate` full 档。

## Open
- 无阻断未决：U-1/U-2 用户已拍板、U-3 位置已确认、U-4 于 14:15 澄清为降级 LLM、U-5 11 族覆盖。
- 否决方案留痕：本地小模型槽位填充、纯 LLM 理解、v1 加词表路线、替换 LLM 通道、编译失败一律拒绝不降级（已由 FR-16 降级链路取代）。
- 范围外独立议题：公共 JSON Schema 变更、run()/decide()/Deps.model 模型分支、LLM 通道 M08 门禁串接、设备 API 真实调用。
- 兼容层/双轨：LLM 通道与确定性通道并行是用户要求的长期双通道，非临时兼容层；`WIDGETPILOT_DETERMINISTIC=off` 可关闭确定性通道；编译能力不足时按 FR-16 降级 LLM，理解层仍零 LLM。
