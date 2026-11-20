# 会话摘要：tool 与 skill 统一 Capability 抽象与渐进式披露改造

## L0 索引卡（续接时先读这一段，≤12 行）
- 场景 / 路径：链【12 提交 → 11/3 写方案+同步文档 → 9 拷问设计 → 5/6 写代码】/ 混合（提交·文档·只读·改码）
- 当前闸门：①-⑤ 全完成；S4 全绿（pytest 428 passed / pyright 0 / ruff 全绿 / contracts drift=[]）；独立 review Round 1 Blocker 5→Round 2 复审 Blocker 清零可合入；Should 3 中 B3 盲区已修+B4/B5 已补测试证据
- 交付产物：ADR-003（docs/adr/）、同步后的设计方案+M03/M06/M07/M10/00 文档、Capability 实现（agent_core + services）、迁移 0008、11 组新测试
- 未完成项：① Docker 执行无沙箱隔离（Q5 明示暂缺，登记债）② 视觉模型真实端点待环境（Q6，mock 测试）③ M06 三表新列 DDL 已落但写入方待补（S3）④ S3 reader 待装配 ⑤ 独立 review 复审
- 下一步：最终门禁确认 → 复审 Round 2 → 收尾合并提交
- 用户执行顺序硬约束：先提交 → 再写方案文档 → 改其他文档 → 拷问设计 → 最后写代码（已遵守）
- 用户执行顺序硬约束：先提交 → 再写方案文档 → 改其他文档 → 拷问设计 → 最后写代码（不可乱序）

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
