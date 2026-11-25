# Change: f76a7264-session-registry-p2 / 2026-11-25 / confirmed

## Why
P1 已把单配方确定性生成接进生产（真三门禁+真成包+四分支分流，review Blocker=0）。用户 18:49 确认继续按 6 阶段计划，本轮 P2 补"这个会话里已生成了哪些 widget、用户说改的是哪一个"。源码核实：widget_bundle 表与 `ix_widget_bundle_session_id` 索引已在（0019），但 `PgBundleStore` 只有 `list_by_user`（无 session 维度）；target 定位器（last/ordinal/by_host/by_content/all）生产代码完全不存在（只有 66 条样本库 + corpus 测试）；IntentCategory 仅 3 值且解释器不消费。缺口性质=查询/定位能力缺失，不是表缺失。

## Delta
- `services/control_plane/bundles/ports.py`：`BundleStorePort` 增 `list_by_session(user_id, session_id, state, limit) -> list[JsonObject]`（租户隔离必带 user_id）。
- `services/control_plane/bundles/pg_store.py` + `tests/support/bundles.py:InMemoryBundleStore`：同步实现 `list_by_session`（过滤 user+session+可选 state，按 created_at 排序，limit 有界）。
- `services/control_plane/bundles/service.py`：`BundleService.list_by_session` 转发（紧接 `list_bundles:266`）。
- 新增 target 定位器（放 `services/guardrails/interpreter/` 或 `services/control_plane/bundles/`）：输入会话内 bundle 列表 + 定位方式（last/ordinal/by_host/by_content/all）+ 槽位条件，输出定位到的 bundle 或"定位不明 + 每个 widget 精准信息（bundle_id/grid/host/data/created_at）"供反问。
- `services/orchestrator/capabilities.py`：`register_capabilities` 增可选关键字参数 `session_query`；在 freeze 前注册 `tool.session.list_widgets`/`tool.session.get_widget_detail`/`tool.session.list_attachments`（`tool.session.*` 前缀，不混入附件工具族；L0 摘要→L1 schema→L2 执行走 invoke.load 同入口）。user_id 由端口预绑定，不信模型传参。
- `services/guardrails/types.py:IntentCategory` 增 `WIDGET_MODIFY`/`WIDGET_DELETE`/`WIDGET_CONTINUE`/`WIDGET_CANCEL`（内部类型）；解释器 `SlotFill.slots` 承接 intent/target/operation（裸串，不改公共 schema）。
- 新增 `tests/unit/test_v3_session_registry.py`：list_by_session（租户隔离/状态过滤/limit/排序）、target 五方式、定位不明反问精准信息、能力注册披露、修改意图解析。

## Invariants
- 无 DB 迁移（0019 表 + 索引已在）；无公共 JSON Schema / events.schema 变更；IntentCategory 是内部类型。
- 租户隔离：所有按会话查询必须带 user_id 过滤；能力 handler 的 user_id 由会话端口预绑定，模型不可传 user_id 覆盖。
- L0→L1→L2 三级披露沿用 REGISTRY 与 invoke.load 同入口；新能力不进附件工具链（`_ATTACHMENT_PREFIXES` 不含 `tool.session.*`）。
- target 定位不明 → clarification 且带**每个 widget 的精准信息**，不只说"无法确定"；定位到目标后修改走 P1 已有三门禁 + 真成包（新版本），不原地改旧 bundle。
- 只查不改：`list_by_session` 不改变 bundle 状态；不扩散到 P3（本地权重）/P4（附件工具链）。
- 全量 pytest 零回归；`register_capabilities` 新增参数有默认值，现有调用点零改动仍可跑。

## Affected
- 代码：`services/control_plane/bundles/{ports,pg_store,service}.py`、`tests/support/bundles.py`、`services/orchestrator/capabilities.py`、`services/guardrails/types.py`、新增 target 定位器模块、`tests/unit/test_v3_session_registry.py`。
- 端口实现者（已核实全量）：`PgBundleStore`（pg_store.py:32）、`InMemoryBundleStore`（tests/support/bundles.py:20）——Protocol 加方法必须同步这两个。
- 注册链：`register_capabilities` 调用点 `services/orchestrator/deps.py:294`；冻结时序 `capabilities.py:555`（新注册必须在 freeze 前）。
- 文档：`docs/adr/ADR-013` §3.5/§8 更新"会话注册表/target 定位"状态一行；`.workbuddy/memory/2026-11-25.md` 追加 P2 结论。
- 无公共契约变更：IntentCategory/RouteLayer 是内部 Python 枚举，不进 JSON Schema；事件仍走现有 AWAITING_INPUT + reason_code。

## Acceptance
- contract_check 六段齐全 PASS；无公共契约/DB 变更声明。
- TDD RED 先失败（方法/符号不存在），GREEN 全过；测试覆盖 list_by_session 租户隔离（跨 user 查不到）、state 过滤、limit 有界、created_at 排序。
- target 五方式各有测试：last 取最新；ordinal 按会话内序号；by_host/by_content 按 spec 槽位匹配；all 全返回；定位不明返回精准信息清单（含 bundle_id/grid/host/data/created_at）。
- 能力注册测试：level0() 可见 `tool.session.*` 摘要、capability_detail 有 args_schema、invoke.load level=2 经端口返回会话 widget 列表；user_id 不可由 args 覆盖（越权 mock 断言）。
- 修改意图解析：描述含"改/修改/继续/取消" → 内部 IntentCategory 对应值；定位后修改走新 bundle 版本（复用 P1 三门禁）。
- 全量 pytest 零回归（基线 802 passed 11 skipped）；ruff 绿 / pyright 0 errors / contracts drift=[] / source_purity 新增目录零污染。
- 独立 review Blocker=0。

## Open
- **本轮架构边界（诚实登记）**：`resolve_target` 与 `tool.session.*` 能力已完成**定义层+端口注入接口+单测**，但生产 L2 消费者未接——原因：全局 REGISTRY 的 handler 无法 per-request 绑定 user_id，项目无 contextvar/请求上下文机制；强行注册会越权或造假。`deps.py:294` 的 `register_capabilities` 本轮未传 `session_query`（保持 fail-closed，能力不注册而非注册后越权）。补 L2 生产接线的前置=引入 per-request 用户上下文（独立基建），登记为后续项，不在 P2 造假绿。
- **`_intent_category`  reaching 边界（诚实登记）**：`rule._intent_category()` 四类映射（取消>继续>修改>生成）已实现且测试覆盖，但 `RuleInterpreter.interpret()` 尚未把它写进 `SlotFill.intent_id`（仍恒 `widget.generic`）——即新 IntentCategory 目前仅定义+单测可达，生产 interpret 不消费。不造假写进 slots；接入点留 P5「修改闭环接 channel」时与 resolve_target 一并做。
- 范围外（6 阶段计划后续）：P3 本地 BIO 权重+命中率；P4 附件全链路；P5 返工+压缩降级（含把 resolve_target 接入 channel 修改闭环）；P6 多配方+N>1。
- 诚实缺口仍在：target 定位器基于 bundle spec 槽位匹配（非真语义理解，靠样本库覆盖）；`current_bundle_ref` "存而可查不可恢复"（ADR-012），本轮只做定位查询不实现版本切换写回。
- 否决留痕：不加 DB 迁移（索引已在）；不改公共 schema/events；不做 REST 端点（本轮只做能力层工具，REST 可后补）；不把 user_id 放进能力 args（防越权）。
- 环境：podman 6.2 在 `D:/apps/podman`；真沙箱测试用 skip 守卫，单独跑。
