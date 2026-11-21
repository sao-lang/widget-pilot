# 会话摘要：tool 与 skill 统一 Capability 抽象与渐进式披露改造

## L0 索引卡（续接时先读这一段，≤12 行）
- 场景 / 路径：本轮【6 依新设计改码】/ full（ADR-004 全级联去类型化，已交付）；历史链【12 提交 → 11/3 写方案+同步文档 → 9 拷问设计 → 5/6 写代码】已完成
- 当前闸门：S4 全绿（pytest 409 passed / 49 skipped / exit 0；ruff/pyright/contracts/contract_check/ledger/removal/source_purity/review_gate 全退出 0）；独立复审 Round 2 Blocker=0；verify.py 因 src/ 布局与缺 mypy/pydocstyle exit 1（环境阻断，已用项目原生命令替代）
- 交付产物：`docs/adr/ADR-004`（全级联去类型化，已实现；`intent_hints.py` 已在 ADR-005 删除）、`docs/adr/ADR-005`（进护栏强化）、`change-contract.md`、`constants.py` 语义族、`sensitive.py`/`validators.py`（新增）、`test_m06_*_hardened/decoupled.py`（新增）、M06/设计方案/00 文档同步
- 未完成项：① 设备 API/续航/沙箱校验未实现（M08 隔离缺失）② verify.py 通用脚本不适配本项目 src 布局（已登记环境阻断）③ 无
- 下一步：用户确认后提交；设备/沙箱阶段另立 ADR，不在本次范围
- 用户硬约束：级联路由整体去类型化、widget 只作 hint；不改 IntentCategory/ReasonCode/事件 schema/SDK 契约

## 环境
- 日期时间：2026-11-21 06:52 GMT+8
- 项目：widgetpilot / 工作区：E:/vsc-workspace/widgetpilot
- OS / Shell：win32 / bash (Git Bash)
- 运行时：Python `.venv/Scripts/python.exe`；Node/pnpm；Docker daemon 可用（compose 文件 docker-compose.dev.yml）
- IDE / Agent：WorkBuddy；主控 conductor

## 场景与 skill
- 场景：执行计划链（见 L0）；当前 ①提交
- 加载的 skill：conductor（主控）、handoff（S0）、grill-design（拷问阶段）、code-quality-review（review 阶段）
- 主控：conductor（唯一流程主控）
- S0 预检降级：preflight.py 报 18 项 PATH 缺失（ruff/pytest/tsc 等），经核为 PATH 探测误报——本项目用 `.venv/Scripts/python.exe -m pytest`、`npx pyright`、项目内 pnpm，命令均可用；不作为阻断。

## S1 路由声明（执行计划）
- 拓扑：链（5 节点，顺序依赖）
- 节点：
  - ①【场景 12/提交】产出：干净基线 commit（工作区在途改造固化）
  - ②【场景 3/补设计 → 文档】产出：统一 Capability + 渐进披露方案 ADR
  - ③【场景 11/文档同步】产出：M03/M06/M07/M10 + 设计方案 §2.4/§11.6/X6/FR-08 口径对齐
  - ④【场景 9/拷问设计，只读】产出：设计问题清单 + 前沿清零结论（闸门：未清零不进⑤）
  - ⑤【场景 5/6/改码】产出：Capability 实现 + TDD + review + 全量回归
- 边/闸门：①→②→③ 顺序；③→④ 文档为拷问输入；④→⑤ 闸门=设计问题清零；⑤ 内含 S4 验证 + S5 独立 review
- 置信度：0.9（用户给出明确顺序算子"先/再/然后/最后"）
- 假设：["①的'先提交代码'指把当前工作区所有在途改造提交为基线，排除 .coverage/.VSCodeCounter/__pycache__ 等噪声；若只想提交特定文件请说"] 
- 待澄清：无（用户已授权三处待拍板点按'最彻底/合理/合适'由 agent 定）

## L1 摘要
### 会话摘要
1. 核查确认 M03 工具调用是断链空壳（registry/invoke 在，生产零注册/零执行链路，run.py:148-152 回灌假 ref）。
2. 核查确认 X6 skill 披露通校验、空内容（need_skill/skill_router/PG 表在，packages/prompts/recipes/ 不存在）。
3. 发现文档三处规程库归属口径打架（skill_router.py:11、M06:241 说 M10 维护；M10:146/266 说 M06/M07 维护）。
4. 用户拍板：tool 与 skill 统一为 Capability 抽象，L0/L1/L2 分级披露，LLM 按需加载；意图路由退为安全门禁。
5. 用户指定执行顺序：先提交 → 写方案文档 → 改其他文档 → 拷问设计 → 最后写代码。

### 关键决策与理由
- 决策：能力暴露从"M06 静态预取决定"转向"LLM 看 L0 目录自主选择" — 理由：解除意图路由与 skill 的因果倒置，落实渐进披露省常驻税的精神。
- 决策（agent 拍板，三项）：① L0 目录放 system prompt 固定槽位保 caching；② `_PREFETCH_MAP` 降为 L0 白名单来源+默认建议、正文加载权交 LLM；③ L2 加载统一计入 X4 四维预算（防绕过）。

### 未完成项与下一步（完整）
- 见 L0「未完成项」；suggested skills：grill-design（拷问）、code-quality-review（review）

---

## 追加记录

### 0655 续：拷问清零 + 实现 + 独立 review fix loop

- 用户诉求：按最彻底方案拍板；顺序=先提交→写方案文档→改其他文档→拷问设计→最后写代码。
- 场景 / 动作：场景链 ①12 提交 ②3 ADR ③11 文档同步 ④9 拷问 ⑤5/6 实现。
- 本轮问题与状态：
  - [已解决] ① 基线提交 — commit `dd54838`（144 文件，排除 .coverage/.VSCodeCounter/.migration-ledger.json，已补 .gitignore）。
  - [已解决] ② ADR-003（docs/adr/）+ ③ 设计方案 §2.4/§11.6/X6/FR-08 与 M03/M06/M07/M10/00 文档口径同步（顶部变更声明 + 关键定义行）。
  - [已解决] ④ 拷问 Q1-Q9 前沿清零（三表加字段 / build_deps 注册冻结 / 直接迁移不留别名 / 三类附件+代码执行+2 skill / Docker 无隔离债 / 视觉模型留接口 mock / 常用控件 recipe / 实现照做验证分级 / 复用 ModelGateway），全部记入 ADR §10.1。
  - [已解决] ⑤ 实现：Capability 抽象 + L0/L1/L2 + LOAD_CAPABILITY 真实执行（replace 假 ref）+ 能力闸门 + 上下文 L0 槽 + 迁移 0008 + 两类 recipe 正文 + 视觉/Docker handler；401→425 passed。
  - [已解决] 独立 review Round 1 Blocker 5（B1 L0 不进上下文 / B2 闸门未接 loop / B3 错误死循环 / B4 L1 未计 token / B5 结果内联回灌）——已逐条修复：catalog 入 Input+assemble、注入 CapabilityGuard 协议、CoreError 转自纠正+拒绝消息注入、L1/L2 均计 token+level Literal、结果落 ObjectStore ref。Should S1（真实 schema L1）/S2（kind Literal）/S3（迁移写入方，登记未完成）/S4（code exec 开关）/S5（S3 reader，登记未完成）已处理或登记。
  - [已否决] S4 建议"生产默认禁用 code exec" — 用户 Q5 明确要求用 Docker 执行代码且明示接受暂无沙箱隔离（ADR §11.5 已登记风险），尊重用户决策不禁用，但 `include_code_exec` 开关已留。
  - [环境阻断] 视觉模型真实端点无 M20 配置 — 接口+生产 adapter 已落地、mock 测试通过，待真实 env 联调（Q6，ADR §11.5）。
- 产物 / 改动：ADR-003；设计方案.md；docs/modules/{M03,M06,M07,M10,00}；packages/agent_core/{types,constants,context/slots,events/seq,tools/{registry,invoke},loop/{decide,run},injection}.py；services/{guardrails/{capability_guard,need_skill,types},orchestrator/{capabilities,capability_guard_adapter,recipe_loader,deps,validate_chain,dispatcher},control_plane/attachments/vision,sandbox_runner/executor}.py；migrations/0008；packages/prompts/recipes/*.md（2）；tests/unit 新增 8 组。
- 闸门 / 下一步：全量 pytest/ruff/pyright/contracts 门禁见 L0 → 独立 review Round 2 复审 Blocker 清零 → 合并提交（节点⑤收尾）。
- 独立 review 报告见下 `## 独立 review 报告`。

## 独立 review 报告

### Round 1：七维 + 接线四问（全新上下文子代理，基线 dd54838）
- 审查人 / 方式：general-purpose 子代理，读 ADR-003 + 全部改动源码 + 测试。
- 结论：Blocker 5 / Should 6 / Nit 4；不可合入，需 fix loop。
- Findings（Blocker，逐条带修复证据）：
  - [B1] L0 目录生产不进上下文（assemble 只在测试传 catalog；run 用 spec.intent）— 已修：`Input.capability_catalog` + run() 装配 assemble + dispatcher `_l0_catalog()`；新增 `test_m03_l0_wiring.py` 端到端断言 messages 含 L0 行。
  - [B2] loop 内 LOAD_CAPABILITY 不过闸门（旧 need_skill 通道与 loop 两套）— 已修：`CapabilityGuard` 协议 + `ProductionCapabilityGuard` 注入 + `SpecState.capability_count/tokens`；新增 `test_m03_capability_guard_wiring.py`。
  - [B3] load() 的 CoreError 无捕获→PEL 无限重投 — 已修：业务拒绝转自纠正（apply_capability_result + 拒绝消息注入 messages），infra 才上抛；测试覆盖。
  - [B4] L1 未计 token、level 非 Literal — 已修：L1/L2 均计 output_tokens；`level: Literal[1,2]` + `kind: Literal["tool","skill"]`。
  - [B5] 结果内联进 tool_result_ref（checkpoint 膨胀 + user role 回灌不可信文本）— 已修：`_store_capability_result` 落 ObjectStore，ref 只存纯 key。
- Fix loop 轮次：1 轮（Round 1 → 5 Blocker 全修）；复审结论见 L0「当前闸门」，Blocker 清零证据=新增端到端测试 + 全量门禁。

### 1523 续：补做原未完成项 3/4 + 独立复审 Round 3

- 用户诉求：问"未完成项 3、4 为什么没做"，核实后确认两项技术上可做（非环境阻断），用户选择"两项都补"。
- 场景 / 动作：场景 5/6 迭代补做 + code-quality-review 复审。
- 本轮问题与状态：
  - [已解决] 项4 S3 reader — `capabilities.py::make_s3_blob_reader`（拆 key#sha256 + get_with_hash_verify，无 hash/篡改 fail-closed）；`build_deps` 注入 `S3ObjectStore()`。测试 `test_s3_blob_reader.py` / `test_build_deps_blob_reader.py`。
  - [已解决] 项3 能力日志写入 — `NeedSkillLogRow`/`SkillPrefetchLogRow` + `pg_stores` INSERT 写迁移 0008 新列；内核 `CapabilityAudit` 鸭子协议 + `Deps.capability_audit` + run.py 三出口记录；服务层 `ProductionCapabilityAudit` 写 need_skill_log（LOADED→decision=None/DENIED→REJECT 兼容 Counter）。测试 `test_capability_audit_adapter.py` / `test_m03_capability_audit_wiring.py` / `test_m06_capability_columns_sql.py`（后者用 mock connect 验证 INSERT 11/9 列参数对齐，真实落库待 PG）。
  - [已解决] 独立复审 Round 3 Blocker 1 — handler 抛 ValueError/KeyError/OSError 非 CoreError 会击穿 run loop（`invoke.load` 未包装）。修：`load()` 捕获 handler 异常，ValueError/KeyError→CoreError.business(CONTRACT_MISMATCH)，其它 Exception→CoreError.infra(SANDBOX_UNAVAILABLE)；补端到端测试断言坏引用转自纠正、不逃逸、留审计。
  - [已解决] Round 3 Should — 审计失败未隔离（run.py `_record_capability_audit` 包 try/except）；CONTRACT_MISMATCH 记为 REJECT（不低报为通过）。
  - [环境阻断] Docker daemon 未运行 — 无法起 PG 做真实落库回归，50 skipped 是集成用例环境守卫；INSERT 结构已用 mock SQL 测试证明，实际落库待 Docker 起来后 `docker compose -f docker-compose.dev.yml --profile core up -d` 重跑。
- 产物 / 改动：见 ADR §11.5 已更新状态与代码阅读指南 §5.3/§9。
- 闸门 / 下一步：最终门禁 → 提交+push（延续用户上次授权的工作流）。

## 独立 review 报告（续）

### Round 3：补做两项的独立复审（全新上下文子代理）
- 结论：Blocker 1 / Should 2 / Nit 2；Blocker 已修待复审。
- Findings：
  - [Blocker] `capabilities.py:76` + `invoke.py:69` — S3 reader 的 ValueError/KeyError/OSError 非 CoreError，穿透 load() 与 run() 的 catch。已修：`invoke.py` handler 调用外包 try/except 转 CoreError（业务/infra 分类），补 `test_load_handler_valueerror_becomes_coreerror` / `test_load_handler_oserror_becomes_infra` / `test_handler_valueerror_self_corrects_without_crash`。
  - [Should] `run.py:_record_capability_audit` 无异常隔离 — 已修 try/except（丢证据不打挂业务）。
  - [Should] `capability_audit_adapter.py` CONTRACT_MISMATCH 记 None（低报通过）— 已修 `is_reject = decision != "LOADED"`。

### 1715 续：技能预取整套删除（用户决策：不留兼容层、不打补丁）

- 用户问"现在还有技能预取吗"→ 核实发现 prefetch() 产物 `ValidationResult.recipe_ids` 全仓零消费者，L0 目录实际来自 registry.level0()，预取是每次请求的空转死代码；用户拍板 A 方案彻底删除 + 纯按需（不需要推荐能力）。
- 删除清单：`skill_router.py`（_PREFETCH_MAP/prefetch/PrefetchResult）、`SkillPrefetchLogRow`/`PgSkillPrefetchLogStore`/port/内存替身、`validate_chain` 的 prefetch 调用+参数+日志+事件字段+recipe_ids 赋值、`/api/skills` 端点、`SkillManifest` openapi schema、`ConfigSnapshot.prefetch_count`；迁移 0009 drop `skill_prefetch_log`（downgrade 补回 0008 的 capability_kind 列 + 0005 索引）。
- 保留：`effective_grid`/`widget`（routing 事件与 ValidationResult 仍用）、skill 事件（只留 need_skill 归因）、need_skill 完整校验链。
- 契约：openapi.yaml 删路径/schema → `pnpm gen:all` 重生成 SDK → contracts:check drift=[]。
- 文档：ADR-003 §13.1 记录决策、FR-7/§8.2/X6/§11.5 更新；M06 代码地图校准（删 skill_router 行、prefetch 主线、行数 2178/460/19 文件）；M06/M07/M04/M02 模块文档加 ADR-003 覆盖声明；M10 的 ConfigSnapshot 四字段口径；设计方案.md 用户旅程。
- 独立复审（全新上下文）：Blocker 清零可合入；Should（0009 downgrade 缺 capability_kind/索引）已修；Nit（M10/M02 文档残留）已修。
- 终验：pytest 387 passed / 49 skipped（Docker 未起）/ pyright 0 / ruff 全绿 / contracts drift=[]。

### 1821 续：need_skill 旧设计整套删除与能力审计归一

- 用户问"怎么还有 need_skill"→ 核实：need_skill_raw 生产唯一调用点（guardrail_adapter）从未传过，模型输出零 need_skill 字段，整条链生产永不执行（单测续命）；但审计表/计数器还顶旧名。用户拍板 A 全删+改名归一 + 同步改写历史迁移。
- 删除：need_skill.py、NeedSkillRequest/Result、validate_chain 的 need_skill_raw/allowlist/turn_state/token_cost 入参与 step15、skill 事件（events.schema kind enum+oneOf、AgentEventKind.SKILL、契约重生成）、相关测试（A13 上限/畸形 JSON/skill 事件断言）。
- 归一：need_skill_log→capability_audit_log（0005 同步改写，recipe_id/depth→capability_id/level 一步到位）、0008 no-op 占位、NeedSkillLogRow→CapabilityAuditLogRow、Store/Port 同步、need_skill_count/tokens→capability_count/tokens、MAX_NEED_SKILL_*→MAX_CAPABILITY_*、need_skill_max→capability_max。
- 前提：用户选"同步改写历史迁移"，已应用旧版本环境需重建库（本阶段无生产数据，Q3 决策）。
- 独立复审：Blocker 清零可合入；Nit（guardrail_adapter 注释仍提 skill 事件）已修。
- 终验：pytest 382 passed / 49 skipped / pyright 0 / ruff 全绿 / contracts drift=[]。
- 文档：ADR-003 §13.2、M06 代码地图（行数 2076/391、测试 23/14/13/4/1/6、8 步主线）、M02/M04/M06/M07 覆盖声明追加。
- **保留**：kind="skill" 的 Capability 族是 ADR-003 新设计，不在删除范围。

### 2005 续：ADR-004 方案文档（只文档未改码）

- 用户诉求：补充场景（天气多接口、设备 API 对接、刷新续航、代码安全）先落方案；`_l1_rule_intent` 去类型化，两张词表扩到覆盖大部分手机 widget 生成意图；明确"暂时不落地"。
- S1 声明：场景 3 / 文档路径 / full / 置信 0.85；子代理=派（3 路独立调研：场景全景 / 代码与测试约束 / 文档落点）；省掉=env_probe、TDD/verify/source_purity（无改码）、git commit、impact_analysis（不改公共契约）。假设 A1–A5 写入 ADR §10 供否决。
- 三路调研关键结论：① 11 类 widget 场景（信息/时间日程/效率/健康/设备控制/媒体/生活/金融/系统/AI生成/通讯）与 8 个正向语义族，数据词只能加权不能设门槛；② 三条硬样本 + 长尾 `"给我整一个能显示日期的小玩意"` 走 L2/L3 是回归地雷，`patterns/intent.py:11` 是第二份负向 regex，`change_gate` 无词表指纹；③ 按 ADR-003 风格新增 ADR-004 并同步四处文档。
- 决策：正向 =（生成/放置/展示动作 AND 宿主/控件对象）或（数据词 × 宿主共现）；负向 = 查询/告知/创作/设备直操/教程/开发语境/裸数据/状态描述 8 族联合条件；保留双循环、正向优先、I14 与 step0 留证链。P1 词表改造 / P2 hints 去类型化 / P3 设备与沙箱（待 M08 隔离）分阶段。
- 产物与行号证据：`docs/adr/ADR-004-意图识别去类型化与补充场景.md:1`（§1–§14、§9 样例之外覆盖、§10 A1–A5、§13 变更记录）；`change-contract.md:1`（六段，contract_check.py 退出 0 / Acceptance=5）；`设计方案.md:6`（v1.9）、`:15`（v1.9 变更说明）、`:210`（X6 口径）；`docs/modules/M06-输入校验护栏与路由域.md:5`（ADR-004 覆盖声明）；`docs/M06-代码阅读指南.md:6/:78/:134`；`docs/modules/00-总览与模块地图.md:5/:52`（ADR 索引）。
- 边界证据：`git status --short` 仅 4 份既有文档改动 + 2 个新增文档，无 `.py`/契约/测试改动。
- 已知未处理：`docs/modules/00-总览与模块地图.md:79` 仍有"按 widget_type+grid 确定性预取"旧口径（ADR-003 遗留不一致，非本轮引入，留给后续文档清理场景）。
- 下一步闸门：用户确认/否决 A1–A5 → 确认后进 P1 改码；未确认前禁止改 `constants.py`。
- S5 收尾（文档轮）：`review_gate.py --overview <本文件> --waived --waiver-reason "场景3纯文档方案"` 退出 0（纯文档，不改码）；`learn_capture.py --dry-run` 退出 0，仅抽到历史条目，本轮无新增可复用经验回写；项目约定与阶段闸门已手工写入 `.workbuddy/memory/MEMORY.md` 与当日日志。

### 2040 续：ADR-004 全级联去类型化改码（场景 6 / 改码 / full）

- 用户诉求：先"级联路由整个都要调整，里面 widget 都要去类型化"，随后确认"意图识别已经做好了，这个任务要改码"。S1 声明场景 6 / 改码 / full / 0.85；S2 先把 ADR-004 升级为全级联口径并固化 change-contract confirmed；子代理=不派（级联强耦合需跨层联想）。
- RED 证据：新增 `tests/unit/test_m06_intent_decoupled.py` 后 `pytest -q` → 16 failed / 7 passed，失败原因均为缺失新行为（宿主锚点未命中、L2 样本含类型词、DENSITY_HINTS 不存在、负向 regex 未同源、指纹缺字段）。
- GREEN 实现：① `constants.py` 正向 P1–P4 语义族、负向 N1–N8 联合条件，新增 `WIDGET_ANCHOR_PATTERNS`/`DENSITY_HINTS`/`DEFAULT_DENSITY_HINTS`/`INTENT_VOCAB_VERSION=v2`；② `semantic_router` 样本 v2 扩至 11 类无类型说法，`_VOCAB` 不再注入类型词；③ `semantic.py` R1 槽位改宿主锚点 OR 开放特征词；④ `grid_resolver` 改 `_density_hints`，旧 `_detect_widget`/`WIDGET_TYPICAL_GRIDS` 删除；⑤ `patterns/intent.py` 负向 regex 从 constants 派生；⑥ `change_gate` 增 `intent_vocab_version`；⑦ 新增 `intent_hints.py` 仅作遥测 hint，`validate_chain._infer_widget` 删除。
- 回归修复：首次全量跑发现 `生成一个翻译任务清单卡片` 被派生负向 regex 的裸 `翻译` 命中；把 N3 收紧为 `翻译(?:这段|一下)` 后全绿（未改测试断言）。
- 门禁证据：`pytest -q` → **405 passed / 49 skipped / exit 0**；`ruff check .` exit 0；改动 9 文件 `ruff format --check` 通过；`npx pyright` 0 errors；`pnpm contracts:check` drift=[]；`removal_check.py --symbols WIDGET_HINTS,WIDGET_TYPICAL_GRIDS,_detect_widget,_infer_widget` PASS；`source_purity.py --baseline <HEAD基线> --scope <9文件> --strict` exit 0；`commitment_ledger.py check --strict` 8/8 收口 exit 0；`contract_check.py` PASS（Acceptance=6）。
- 环境阻断（非回归）：`verify.py --lang python` exit 1——通用脚本假设 `src/` 布局且需要未安装的 mypy/pydocstyle，`ruff format --check .` 会重排 235 个历史文件；已用项目原生命令替代，未改断言造假绿。
- 文档同步：`设计方案.md` v1.9 改为已实现；ADR-004 状态已实现；M06 权威文档/代码阅读指南/模型缺口表/M4-M6 速查表/00 总览（顺手修正"按 widget_type+grid 预取"旧口径）已更新。
- 下一步：S5 独立复审（全新上下文子代理七维）→ 清 Blocker → `review_gate.py` 退出 0 → 收尾。

### 2050 续：独立复审 Round 1 与 fix loop

- 复审（全新上下文子代理）结论：不可合入，Blocker 1 / Should 6 / Nit 3。
- Blocker B1：`check_input()` 没有正向优先，`patterns/intent.py` 拼入全量负向表后，N1「数据词 + 疑问」把 `能帮我在桌面加个天气吗？` 二次误拒（L1 已放行但 step7 仍判 OUT_OF_SCOPE）。修复：constants 拆出 `INTENT_GUARD_PATTERNS`（同源子集，N1/N4 自带「宿主 + 生成动词」联合排除），`patterns/intent.py` 改从该子集派生；`test_m06_acceptance.py::test_a3_legitimate_widget_not_rejected` 补疑问句端到端断言。
- Should 处理：N6 开发语境提到 `_l1_rule_intent` 正向循环之前（S2）；`test_change_gate_tracks_intent_vocabulary` 补 `diff_config` 断言（S4）；ADR FR-2/FR-9 口径修正（S1/S3）；M06 模块文档正文旧类型化口径与设计方案/00 总览"未改码"声明同步（S5/S6）；代码阅读指南与速查表字段/行数同步（N1–N3）。
- 修复后证据：`pytest -q` **406 passed / 49 skipped / exit 0**；`ruff check .` exit 0；9+2 改动文件 `ruff format --check` 通过；`pyright` / `contracts:check` / `removal_check` / ledger / contract_check 复跑见下节。
- 下一步：派**新的**全新上下文子代理复审 Round 2（禁止复用 Round 1 报告）→ Blocker 清零后跑 `review_gate.py` 收尾。

### 2100 续：最终门禁与独立复审收口

- Round 2（全新上下文子代理）总判定：可合入，**Blocker = 0**；Should 2（词序边界误拒 + 文档数字漂移）、Nit 2（模块正文旧口径 + ADR 关联行号）。
- Round 2 修复：`_NEG_WIDGET_GUARD` 从正向先行断言改为**整句锚定 `^` + 负向先行断言**（疑问句中宿主/动作无论前后都不会误拒），新增 3 条词序变体测试；文档数字统一为 `constants.py=161`、`validate_chain.py=382`、`change_gate.py=44`、全量 `409 passed / 49 skipped`，M06 模块正文旧口径与 ADR 关联行号同步。
- 最终证据：`.venv/Scripts/python.exe -m pytest -q` → **409 passed / 49 skipped / exit 0**；`ruff check .` exit 0；改动文件 `ruff format --check` 通过；`npx pyright` 0 errors；`pnpm contracts:check` drift=[]；`contract_check.py` PASS；`commitment_ledger.py check --strict` 8/8；`removal_check.py` PASS（4 个旧符号零残留）；`source_purity.py --baseline HEAD --scope 改动文件 --strict` exit 0。`verify.py --lang python` 仍因 src/ 布局与 mypy/pydocstyle 缺失 exit 1（环境阻断，已用项目原生命令替代）。
- 工作区：`git status --short` 含 11 个改动文件 + `tests/unit/test_m06_intent_decoupled.py`、`services/guardrails/intent_hints.py` 两个新增文件 + ADR-004 / change-contract（本会话文档轮已建）。无契约源、SDK、生产数据改动。

## 独立 review 结果

- ADR-004 Round 1（全新上下文子代理）：发现 1 项确定性误拒（疑问句 `能帮我在桌面加个天气吗？` 在 check_input 二次拒绝）+ 6 Should + 3 Nit；已按 fix loop 修复。
- ADR-004 Round 2（再次全新上下文子代理）：总判定可合入，**Blocker = 0**；留 2 Should / 2 Nit，均已处理。
- ADR-005 Round 1（全新上下文子代理）：发现 1 项 PII 真值校验漏报 Blocker（首个匹配无效即跳过整规则）+ 5 Should + 6 Nit；已改为 `re.finditer` 并补回归。
- ADR-005 Round 2（再次全新上下文子代理）：总判定可合入，**Blocker = 0**；3 Should / 2 Nit 已处理（Blocker 回归样本改为无效邮箱在前、有效邮箱在后；文档五类/枚举/数量统一；外置词库措辞与 overview 更正）。
- 最终状态：Blocker = 0；机器门禁全绿（见上节）；13/13 承诺收口。

## 独立 review 报告

### Round 1 摘要（全新上下文）
- Blocker：`check_input()` 无正向优先，`patterns/intent.py` 拼接全量负向表导致疑问句合法 widget 请求被二次误拒（`validate_chain` 端到端复现）。修复：`INTENT_GUARD_PATTERNS` 同源子集 + N1/N4 联合排除 + 端到端断言。
- Should：FR-2 数据词开放口径、N6 开发语境被正向吞掉、L2 阈值未重校准、`diff_config` 未测、模块正文旧口径、"未改码"残留。Nit：指纹字段数、槽位描述、行数漂移。
- 删除推导：4 个待删符号代码零残留（PASS）。

### Round 2 摘要（全新上下文）
- 结论：可合入，Blocker = 0。独立实测 FR-1～FR-13 除 FR-4 词序边界外全通过；契约形状（IntentCategory/RouteLayer/ReasonCode/事件键/fallback 串/ValidationResult.widget_type）未变；4 个待删符号生产代码零残留；无改断言造假绿。
- Should 1：`_NEG_WIDGET_GUARD` 用正向断言、位置敏感，疑问词在宿主前仍误拒（如 `天气怎么样啊，我想在桌面加一个`）。已改为 `^` 锚定 + 负向断言并补 3 用例，pytest 409 passed。
- Should 2：代码阅读指南与速查表行数/测试数字漂移。已统一为实际行数与 409 passed。
- Nit：M06 模块正文第 31 行旧预取口径、ADR 关联行号。均已同步。

### ADR-005 Round 1 摘要（全新上下文）
- Blocker：`rule_registry.scan` 用 `re.search` 只取首匹配，首匹配真值校验失败即 `continue` 跳过整条规则，导致「无效号在前、有效号在后」漏报真实 PII（且快照已写原文）。修复：`re.finditer` 遍历全部匹配，任一有效即命中；补混合邮箱回归用例。
- Should：注入实 8 条非 9（全链数字更正）；M06 规则表补齐 5 条新规则；间接注入/凭据外泄补测；外置词库加载与 fail-fast 补测；无效邮箱样本改为真值校验拒绝而非正则不匹配。
- Nit：SENSITIVE 沿用 GUARD_INPUT_HIT 注释、外置词库按类别覆盖措辞、首个命中语义、recipes frontmatter 去 widget_type、overview 更正、合法 token 控件不误伤。

### ADR-005 Round 2 摘要（全新上下文）
- 结论：可合入，Blocker = 0；独立实测确认 `re.finditer` 修复有效（无效邮箱在前、有效邮箱在后仍能命中）；代码无具体涉政词，POLITICAL 词库为空且外置；去类型化生产代码零残留。
- Should：Blocker 回归样本原用 `12345678901`（正则第二位不匹配）无法守住修复，已改为 `user@example..com` + `user@example.com`；M06 权威文档 GuardHitType 三处补 `SENSITIVE`；四类/五类口径统一；外置词库措辞与 overview L0 更正。均已处理。

### 2110 续：ADR-005 进护栏强化 + 去类型化收尾（场景 5 / full）

- 用户诉求：进护栏注入/PII 两表力度不够，评估成熟库；新增涉黄/涉暴/涉政敏感词；全仓残余 widget 类型化（点名 `_HINT_PATTERNS`）清除并同步 ADR-004。
- 库选型：LLM Guard 已归档、Guardrails AI hosted 下线、NeMo 太重、Presidio 中文需 NLP 模型；采用 phonenumbers / id-validator / email-validator 做 PII 真值校验，pyahocorasick 做敏感词 DFA。依赖写入 `pyproject.toml` + `uv.lock`，stub 放 `typings/`。
- RED：新增 `test_m06_input_guard_hardened.py` 收集期即失败（缺 `services.guardrails.sensitive`），其余用例均为缺失新行为；GREEN 后 429 passed / 49 skipped。
- 实现：注入 3→8 条；PII 形状命中后真值校验（修正 `test_m06_completeness.py` 校验位错误的旧身份证夹具）；新增 `sensitive.py`/`sensitive_lexicon.json`（PORN/VIOLENCE 内置、POLITICAL 外置且代码不内置具体词）；`GuardHitType.SENSITIVE` 只留 `<sensitive:CATEGORY>`；删除 `intent_hints.py`，`widget_type` 恒 `custom`。
- 门禁：pytest 429 passed / 49 skipped exit 0；ruff check 绿；pyright 0 errors；contracts drift=[]；contract_check PASS；removal_check 5 符号 PASS；source_purity HEAD 基线 + scope --strict exit 0；commitment_ledger 13/13。依赖变更需重建镜像（Docker 未起，环境阻断）。
- 文档：新增 ADR-005；同步 ADR-004（hint 段与状态）、设计方案 v2.0、M06 权威文档/指南/缺口表、M4-M6 速查表、00 总览 ADR 索引、change-contract 合并契约。
- 下一步：派全新上下文子代理独立复审 → fix loop → review_gate → 收尾。
- 更正（复审 N5）：本会话 L0 中「intent_hints.py（新增）」是 ADR-004 阶段历史；ADR-005 已删除该模块与 `_HINT_PATTERNS`，`widget_type` 恒 `custom`。注入规则实际 **8 条**（3 原有 + 5 新增），此前「9 条」为笔误，已全文更正。
- 版本动作：2026-11-22 提交 `3cb96a9 feat(m06): 意图级联去类型化并强化进护栏检测`（37 files，+1314/-127），Hooks 通过；待 push。
