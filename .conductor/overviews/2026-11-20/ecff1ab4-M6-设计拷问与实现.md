# 会话摘要：M6 设计拷问与实现

## L0 索引卡（续接时先读这一段，≤12 行）
- 场景 / 路径：链【场景9 拷问(只读)→场景3 补充设计(文档)→场景4 开发(改码)】——**全链完成**
- 当前闸门：**全绿**（276 passed / M06 69 用例 / 96% 覆盖 / ruff+format 0 错 / 独立 review Blocker 清零）
- 交付产物：`docs/modules/M06-*.md`(v1.5 修正)、`docs/runbook/m06-docker-and-model-testing.md`、services/guardrails/ 16 模块 + orchestrator/validate_chain.py、tests/unit/test_m06_*.py(8 文件)
- 未完成项：仅剩**联调项**（PG guard_hit 持久化、L2/L3 真实模型注入）——见 runbook §2/§3，不阻塞交付
- 下一步：无（会话已收尾）；后续接手先读 runbook 再动 docker/模型
- 环境注意：**本机验收 = `docker compose --profile core up -d` + `.venv/Scripts/python.exe -m pytest -q`，实测 276 passed（含 14 真实 PG/Redis 集成）**。项目 MEMORY 的「166 passed」为旧基线，本次已更新。CI 不作验收依据。

## 环境
- 日期时间：2026-11-20 22:26 GMT+8
- 项目：WidgetPilot / 工作区：E:/vsc-workspace/widgetpilot
- OS / Shell：Windows / Git Bash
- 运行时：Python 3.12+（venv 实测 cpython-313 缓存）、pytest / uv
- IDE / Agent：WorkBuddy / conductor 主控

## 场景与 skill
- 场景：9→3→4 链；主控 conductor；工具 grill-design（拷问阶段已用）
- 加载：grill-design；后续 coding 加载 coding-rules + engineering-tdd

## L1 摘要（需要背景时读）

### 会话摘要（≤10 条要点）
1. 用户诉求：先看总设计 + M06 模块设计，拷问 M06；有问题先改设计，没问题再 coding；docker 未起可先写代码，但要留标记供用户/后续 AI 用 docker 测试。
2. 代码现状（Explore 实测）：M06 **greenfield**——设计要求的 11 个服务模块 + 配套 0 实现，测试 0；可复用资产仅契约枚举 `packages/contracts/enums.py`、M03 规则原语、FakeModel call_count 桩。
3. 归属核查：四级级联**全部归 M06 编排**，但 L2 embed / L3 classify 的**能力经 M20 Provider 抽象**（M20 §R8/§6.3），L4 主 LLM = M07 `ModelGateway`（M07 §4.3）；M06 只做级联判定与留证，不实作模型端点。
4. 用户三项决策（AskUserQuestion 回答）：
   - 级联范围：**按设计文档实现，对比其他模块确认归属**（已核查：M06 编排四级，能力借 M20/M07）。
   - L2 embedding：**留真实模型可替换口子**，模型文件后续真实环境提供，与 docker 测试标注到一起。
   - need_skill：**不妥协，按设计完整实现**；设计文档有问题就改文档再 coding，保证功能完整。

### 拷问发现的问题清单（带状态）
> 详见本文件；Blocker 3 / Major 7。

**Blocker**
- [待解决] B1 红线自相矛盾：§0.4/§3.6「护栏与 LLM 无网络边、不持有 provider 凭证」vs D-8 L3/L4 经 ModelGateway 调模型。修法：红线限定为「**拒绝类路径 0 模型调用**」，放行方向 L3/L4 经 M20/M07 注入端点，凭证由 provider 侧持有。
- [待解决] B2 need_skill 悬空契约：M06 三处声称 M03 提供 `parse_need_skill`，M03 文档/代码均无。修法：M06 自实现解析（用户决策），改文档去掉虚假引用。
- [待解决] B3 L2 无模型来源：infra/models 不存在、M01 未挂载、样本库未定。修法：定义 `Embedder` Protocol + 可注入实现，真实模型文件后续提供，与 docker 标记同处登记。

**Major**
- [待解决] M1 ValidationResult 三处定义不一致（§1.4/§3.2 无 grid_source；§4.3 有；§6.2 无），A20 断言却要 grid_source 非空。
- [待解决] M2 GridSource(selector/explicit_text/clarified) 与 GridResolution.status(…/missing) 两套编码混用，映射未定义。
- [待解决] M3 M06 GuardHit(大写枚举) 与 M03 types.py GuardHit/_HitType(小写蛇形) 撞名词表不同。
- [待解决] M4 A14 FPR 分母（10/100）与 §3.4 E 公式（被拦应放行/应放行总数）对不上。
- [待解决] M5 A4 PII「脱敏放行(call_count>0)」与「拒绝路径 call_count==0」矛盾；GuardHit.decision 有 MASK 值却无路径定义。
- [待解决] M6 §1.6 关键设计原则两个「9.」编号重复。
- [待解决] M7 A26 插在 A19/A20 中间，编号乱序。

### 验证结果
- 尚未 coding，无测试证据。preflight：ruff/mypy/pytest 等在 venv 内（裸 python 路径检测缺失，以 venv 为准）。

### 未完成项与下一步（完整）
- 任务1 核查级联归属：✅ 完成（结论见 L1 第3条）
- 任务2 修 M06 文档 3B+7M → 任务3-6 TDD 实现 → 任务7 A1-A26 → 任务8 测试标记 → 任务9 独立 review + overview 收尾

## 追加记录

### 2245 续：M06 实现完成 + 两轮独立 review 修复（收尾）
- 用户诉求：拷问后若有问题先改设计再 coding（已执行，见上方 L1）。
- 场景 / 动作：场景3 修文档 → 场景4 greenfield 开发 → 独立 review fix-loop。
- 本轮问题与状态（逐条）：
  - [已解决] 设计 3 Blocker（L3/L4 红线冲突 / need_skill 悬空 / L2 无模型）+ 7 Major → v1.5 文档修正（路径见 L0）。
  - [已解决] Review Round 1：Blocker 1（A3 越界装配层未兑现，测试假绿）+ Should 6 + Nit 3 → 全部修复（is_out_of_scope 前置短路、邮箱掩码、DefaultEmbedder 共享词表、事件 payload 补全、need_skill+三日志装配 telemetry.py、clarified 溯源、A16 gate、hash 链 lock+全字段）。
  - [已解决] Review Round 2 新增 Should 3 + Nit 2（合法控件词误判越界 / threshold_hit 误报 / A3 短路缺留证 / need_skill 日志口径 / hash 字段）→ 全部修复并复审确认 Blocker 0/Should 0/Nit 0。
- 产物 / 改动：
  - 代码：services/guardrails/{types,constants,rule_registry,input_guard,semantic,grid_resolver,spec_conflicts,semantic_router,intent_classifier,intent_cascade,skill_router,need_skill,guard_hit,telemetry,change_gate,validate_chain}.py + patterns/{injection,pii,intent}.py；services/orchestrator/validate_chain.py（真实现）。
  - 测试：tests/unit/test_m06_*.py（8 文件，69 用例，A1-A26 全覆盖 + review 修复断言）。
  - 文档：M06 设计文档 v1.5；docs/runbook/m06-docker-and-model-testing.md（docker/真实模型联调标记）。
- 闸门 / 验证证据：
  - 测试：`.venv/Scripts/python.exe -m pytest -q` → **276 passed**（含 14 真实 PG/Redis 集成；docker compose core 实测 daemon up）。
  - 覆盖率：`python -m pytest --cov=services.guardrails tests/unit/test_m06_*.py` → **TOTAL 96%**（api/main.py 预置健康壳 0% 不计）。
  - Lint/Format：`ruff check`/`ruff format --check`（M06 范围）→ All checks passed / 32 files already formatted，退出码 0。
  - 独立 review：2 轮全新上下文子代理，最终 Blocker 0 / Should 0 / Nit 0，可交付。
  - verify.py 环境说明：0/5 是工具模板不匹配（无 src/ / mypy/pydocstyle 未预装 / 全库预置 6 条 ruff 问题在 packages/），非本模块回归；工具已补装，范围证据如上。
- 联调待办（不阻塞）：PG guard_hit 持久化（runbook §2）、L2/L3 真实模型注入（runbook §3）。

## 独立 review 结果（最终 · Blocker 清零）
- 审查方式：2 轮全新上下文子代理 + 1 轮快速确认（均实际读源码 + Python 复现），非执行者自查。
- 最终结论：**Blocker = 0，Should = 0，Nit = 0，可交付**（Blocker 已清零，无阻塞项）。
- Blocker 清零证据：
  - Review Round 1 的 Blocker 1（A3 装配层未兑现）→ 已修 `is_out_of_scope` 前置短路，复审 Round 2 确认解决；
  - Review Round 2 新增 Should 3 / Nit 2 → 全部修复，第三轮子代理实测确认 Blocker 0 / Should 0 / Nit 0；
  - 测试证据：`.venv/Scripts/python.exe -m pytest -q` → 276 passed，退出码 0；M06 69 用例全绿。
- 报告正文见下方「独立 review 报告」章节（Round 1 / Round 2 完整分级）。

## 独立 review 报告
### Round 1：七维 + 功能断点/假绿专项
- 审查人 / 方式：全新上下文子代理 / 源码精读 + Python 实测复现
- 结论：Blocker 1 / Should 6 / Nit 3；初判不可交付
- Findings（摘要，完整四段式见审查记录）：
  - [Blocker] A3 越界在装配层未兑现且测试假绿 — orchestrator/validate_chain.py — 修：is_out_of_scope 前置 + 测试走装配断言 reason_code/0 模型调用。
  - [Should] 邮箱 PII excerpt 未掩码 — rule_registry.py — 修：_mask 按 rule_id 分治。
  - [Should] DefaultEmbedder 不同文本恒 1.0 — semantic_router.py — 修：共享 bigram 词表定长向量。
  - [Should] 事件 payload 缺字段 / model_tier 硬编码 — validate_chain.py — 修：补全 §4.2 字段。
  - [Should] need_skill 未装配 + 三日志表缺失 — 修：新增 telemetry.py 并装配。
  - [Should] A21 clarified 无路径 / A16+A25 假绿测试 — 修：clarified_grid 入参 + gate/stats 真判据。
- Fix loop 轮次：1；复审结论见 Round 2。

### Round 2：修复确认 + 新回归
- 审查人 / 方式：全新上下文子代理 / 逐条验证 + 边界复现
- 结论：上一轮 Blocker 已解决；新增 Blocker 0 / Should 3 / Nit 2
- Findings：
  - [Should] 合法控件描述（含天气/聊天词）被越界短路 — intent_cascade.py — 修：正向词表优先。
  - [Should] routing.threshold_hit 在 L3/L4 误报 True — types.py+validate_chain.py — 修：IntentDecision 加 threshold_hit 字段透传。
  - [Should] A3 短路缺 guard_hit 留证 — validate_chain.py — 修：补 log_hit + 事件四字段。
  - [Nit] need_skill 日志 count/token 口径 / hash 字段不全 — 修：统一决策后状态 + hash 并入 session/run/should_pass。
- Fix loop：全部修复；第三轮快速确认（子代理实测）→ Blocker 0 / Should 0 / Nit 0。Blocker 清零证据：276 passed + review 复审结论。

## 交接
- 脱敏声明：本文件不含密钥 / token / PII
