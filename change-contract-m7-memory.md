# Change: M7 记忆与上下文全量落地 / 2026-11-21 / confirmed

## Why
用户判定 M7 的「记忆管理与上下文管理不完善」。调研（overviews/...社区调研.details.md）与盘点（...M7现状盘点.details.md）证实：设计文档（M07 §3.4 E、ADR-002）完整且与社区主流（Mem0/Zep/LangMem）方向一致，但生产实现几乎全缺——无 `UserPreference`/`PreferenceEvent` 表与类、无 REST 端点、无偏好学习逻辑，`packages/agent_core/context/slots.py` 用硬编码中文串冒充六槽位装配，M06/M04/M05 交付的字段在生成回路里被丢弃（B1–B8 八个接线断点）。用户拍板：M7 全量承诺一次做完 + 新增轻量程序记忆 + 槽位接真实端口并标注缺口 + 三类写入信号实装。当前无真实模型端点/key，凡依赖模型处必须留真实接线、测试用 mock、缺口写追踪文档。

## Delta
- 新增 PG 迁移：`user_preference`（证据化字段 + version）、`preference_event`（只追加、失效不删行、hash 链）、`user_procedure`（轻量程序记忆，零 LLM 采纳信号）。
- 新增 `services/orchestrator/memory/`：真实 PG `PreferenceStore` / `PreferenceEventStore` / `ProcedureStore` + 四操作纯代码学习（`ADD`/`UPDATE`/`NOOP`，权重不变量 I-M3）+ 检索期衰减 + 高置信门禁；禁止内存/Fake 桩进生产源码。
- 新增 REST 偏好读写清三端点（本人、租户隔离），契约入 `packages/contracts/` 并走四同步生成。
- 装配层新增 `RetrievalPort` / `AttachmentInsightPort` / `PreferencePort` / `ProcedurePort` 真实协议，`assemble` 从端口取真实数据；端口未注入则省略该槽（部署形态），生产装配路径关键端口缺失即 fail-fast；禁占位串冒充。
- 移除 slots.py 装配函数中的硬编码中文占位串（字面量无具名符号，以字符串内容核对）。
- 新增上下文流水线：检索槽混合召回→强制 rerank→证据包化→标 UNTRUSTED DATA；大对象 >20k token 落 S3 留 `object_key + 预览`；压缩只砍 tool message、必留任务契约与 `excluded_approaches`；分层 token 预算。
- 新增轻量程序记忆：`UserProcedure`（recipe/widget 采用次数与最近采用时间，只从显式采纳行为取信号，零 LLM），第 2 槽注入加权提示；同步改 ADR-002（撤销 §6「不做 procedural」）与 M07 文档。
- 补齐接线断点：B1 `preferred_grids` 从 `PreferenceStore` 注入 `validate_chain`；B2 `ValidationResult` 全字段进 `run()` 的 `Input`；B3 `clarified_grid` 回注 `State.clarification_answer`；B4 L4 `IntentGateway` 经前沿档位接线（真模型留端点、无 key fail-fast、测试 mock）；B5 模型分档按 tier 选端点；B6 偏好 REST/存储/学习；B7 `cost_ledger` + `repro_fingerprint`（含 `preference_version`）落库；B8 上下文装配去占位。
- 修正违反 coding 规则：`packages/agent_core/loop/run.py` 为「单测桩」兜底的 `isinstance` 转换改为真实契约断言/fail-fast。
- 修正违反 coding 规则：`errors.assert_partition` 移除仅为兼容旧调用的占位入参（无具名删除符号，函数签名收窄）。
- 新增 `docs/modules/M07-模型依赖与测试缺口标注.md`（模板 M04/M06）：逐项登记 embedding/rerank/分档端点/Responses wire/真实生成质量因无模型无法真测的缺口与验收清单；非模型缺口（记忆表/REST/装配）单列不混算。
- 后继模块文档标注：`docs/modules/M10-知识检索与评测域.md`（RetrievalPort 真实实现 + embedding/rerank 端点）、`docs/modules/M05-附件处理域.md`（AttachmentInsightPort）、`docs/modules/M08-出护栏契约校验与沙箱测试域.md`（correction 纠偏信号）、`docs/modules/M09-产物与包管理域.md`（采纳产物信号 + bundle_ref 快照）、`docs/modules/M20-LLMProvider模型接入与切换.md`（分档端点 + L4 前沿档），并在代码功能占位处注释。

## Invariants
- D-5 零 Agent 框架；记忆学习 `call_count == 0`（I-M2）；不存历史代码/附件原文/PII 原文（I-M1）；低权重不覆盖高权重（I-M3）；清除后读取为空且 `version+1`、事件保留可审计（I-M4）；租户隔离（I-M7）；`preference_version` 进复现指纹（I-M6）。
- 六槽位（+ADR-003 L0 能力目录）顺序固定保 caching；system 前缀逐字不变、append-only；压缩必留任务契约与 `excluded_approaches`；检索文本恒标不可信数据不执行。
- ValidationResult 不重复校验；L4 只做放行方向归类、禁作安全裁判；护栏命中/挂起不直进模型。
- 契约（events/errors/enums/openapi/Python SDK/TS SDK）不改语义；如新增偏好端点须走四同步生成且 `pnpm contracts:check` drift=[]。
- 生产源码零 Fake/InMemory/Stub/测试桩；模型处留真实接线、缺 key fail-fast，不降级 fake；测试通过显式注入 mock。
- Docker 真实环境可用，集成测试真连 PG/Redis；skip 守卫仅用于环境缺失，不得改断言造假绿。

## Affected
- 代码：`services/orchestrator/memory/*`（新）、`services/orchestrator/{deps,dispatcher,guardrail_adapter,validate_chain,capabilities}.py`、`packages/agent_core/context/{slots,budget}.py`、`packages/agent_core/loop/run.py`、`packages/agent_core/types.py`、`services/control_plane/{models,api/server}.py`、`services/control_plane/migrations/versions/*`（新迁移）、`packages/contracts/*`（偏好端点）、`services/guardrails/ports.py`（L4 接线）。
- 测试：`tests/unit/test_m07_*`（记忆四操作/衰减/门禁/程序记忆/装配端口）、`tests/integration/test_m07_*_pg.py`（真 PG 偏好/事件/程序记忆），沿用 conftest 内存桩仅作显式注入。
- 文档：`docs/modules/M07-生成编排与成本域.md`、`docs/adr/ADR-002-记忆管理机制.md`、`docs/M07-代码阅读指南.md`（新建）、`docs/modules/M07-模型依赖与测试缺口标注.md`（新建）、`docs/modules/M0{5,8,9,10,20}-*.md`（接线义务标注）、`docs/modules/00-总览与模块地图.md`。
- 影响面：dispatcher→run→assemble 主链、guardrail_adapter 偏好注入、control_plane 路由与 schema、契约生成链；需 `impact_analysis` 核实调用方。

## Acceptance
- `.venv/Scripts/python.exe -m pytest -q` 退出码 0（2026-11-21 实测 **515 passed**，基线 478 → +37；真实 PG/Redis 集成用例真跑）；新增 37 个 M07 用例覆盖四操作/衰减/门禁/程序记忆/装配端口/断点接线/成本账/复现指纹。
- `ruff check .`、`npx pyright --pythonpath .venv/Scripts/python.exe`、`pnpm contracts:check` drift=[]、`contract_check.py`、`commitment_ledger.py --strict`、`source_purity.py --strict`、`review_gate.py` 全退出 0；新增代码零 type ignore。
- 生产源码扫描：无 `UserPreference` 等内存桩、`slots.py` 无中文占位串、`run.py` 无单测桩兜底；`grep -rl "【M07 协同】" docs/modules/` 列出全部被标注模块。
- ADR-002 §6 不再含「不做 procedural memory」；`user_preference`/`preference_event`/`user_procedure` 三表在 PG 中可查；REST 三端点可达且租户隔离。
- B1–B8 逐条有代码证据与测试；模型缺口文档逐项登记且非模型缺口单列；Docker 起真实环境验证通过。

## Open
- 影子校准初值（权重阈值 0.7、衰减 1.5/0.3、TTL 180 天、四维预算上限、分档阈值）沿用设计文档「影子校准中」口径，本轮取文档建议初值，后续 M10/M11 校准回填——责任人 M10/M11，退出条件=影子期统计完成。
- Responses API wire 仅留真实接线点（M20 adapter），本轮不实装双 wire——责任人 M20，退出条件=M20 接入双 wire 后补测。
- `correction` 写入信号依赖前端/M08 纠偏事件，本轮仅在 M08 文档标注 + 代码注释占位——责任人 M08，退出条件=纠偏事件上线后接线。
