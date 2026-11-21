# 统一 Capability 抽象与渐进式披露 · 代码阅读指南

> 面向第一次接手本次改造（commit `e598b60`，ADR-003）的开发者。目标：用最短路径
> 建立「tool 与 skill 如何统一、L0/L1/L2 如何流动、闸门如何守住安全」的骨架认知。
> 本指南是**阅读顺序建议，不新增任何设计决策**；权威规格见
> `docs/adr/ADR-003-统一能力抽象与渐进式披露.md`，行为判据见 `tests/`。
> 既有模块背景见 `docs/M03-代码阅读指南.md` / `docs/M06-代码阅读指南.md`。

---

## 0. 先建立整体认知

这次改造把原先**两套互不相通**的能力机制合为一个：

| 改造前 | 改造后（ADR-003） |
|---|---|
| M03 `ToolSpec` + `CALL_TOOL`，生产零注册、run loop 回灌假 ref `tool-result/...` | 统一 `Capability`（`kind=tool\|skill`）+ `LOAD_CAPABILITY`，真实执行 handler |
| M06 `need_skill` + `(widget_type,grid)` 静态预取决定加载什么 | 意图路由退为**安全门禁**；LLM 看 L0 目录经 `LOAD_CAPABILITY` 自主选择 |

核心是**三级披露**：模型在 L0 只看摘要，按需请求 L1 详情（schema/manifest），
再请求 L2（执行工具 / 注入规程正文）。任何 L1/L2 都必须过 M06 闸门并计入预算。

规模：本次提交 44 文件 / +2002 −170 行；涉及 M03 内核、M06 护栏、M07 上下文、
M05 附件、M08 代码执行、M10 规程库。**不要按文件名字母序读**，按下面 6 阶段读。

```
阶段 1  数据模型与抽象      types.py → tools/registry.py
   ↓
阶段 2  加载执行核心  ★    tools/invoke.py → loop/decide.py → loop/run.py
   ↓
阶段 3  依赖边界           injection.py（CapabilityGuard 协议 + Deps 新字段）
   ↓
阶段 4  安全闸门           guardrails/capability_guard.py → need_skill.py → 适配器
   ↓
阶段 5  装配与能力实现      orchestrator/{capabilities,deps,dispatcher,recipe_loader}
                          + attachments/vision.py + sandbox_runner/executor.py
   ↓
阶段 6  测试即文档          9 组能力主题测试 + 3 组改造的既有测试（端到端接线证据）
```

---

## 1. 阶段 1 · 数据模型与抽象（先扫，再精读 registry）

### 1.1 `packages/agent_core/types.py`（977 行）—— 只看改动处

| 位置 | 改动 | 读什么 |
|---|---|---|
| `SpecState`（:92-114） | 新字段 | `capability_id`、`level: Literal[1,2]`、`capability_count`、`capability_tokens`——可信结论槽位，模型不可信输出经白名单写入 |
| `Decision`（:348-350） | 字段替换 | `tool` → `capability_id` + `level`，`args`/`surface` 保留；**旧字段全部删除，无兼容别名** |
| `DecisionKind`（:271 附近） | 枚举替换 | `CALL_TOOL` → `LOAD_CAPABILITY`（`CheckpointNode` 同步） |
| `Message`（:574 附近） | 多模态扩展 | 新增 `image_ref`；`to_dict()` 有图时用 content parts（复用 ModelGateway，不另起 VisionPort） |

**关注点**：`level` 与 `Capability.kind` 都是 `Literal` 类型——非法值在类型层就被挡住，
不靠运行时提示。旧键名对照：`CALL_TOOL`→`LOAD_CAPABILITY`，`spec.tool`→`spec.capability_id`。

### 1.2 `packages/agent_core/tools/registry.py`（115 行）—— 精读，全库能力单一真相

```
:22  Capability          —— 统一数据类（id/kind/brief/detail_ref/payload_ref/surfaces/cost_hint + args_model/handler/body）
:56  register()          —— 启动期注册，冻结后抛 CoreError
:63  freeze_registry()   —— 冻结，运行时不可增删（接线四问之 1）
:80  level0()            —— L0 视图：只含 id/kind/brief/cost_hint，不泄漏 schema/正文
:88  capability_detail() —— L1 视图：返回真实 args_schema / manifest（非占位串）
```

**关注点**：`level0()` 与 `capability_detail()` 是**渐进披露的边界**——前者决定模型常驻看到什么，
后者决定 L1 能拿到什么。读这两个函数时对照 `test_level0_has_only_brief_fields`。

---

## 2. 阶段 2 · 加载执行核心（全改造最重要，逐行读）

### 2.1 `tools/invoke.py`（77 行）—— `load()` 五步

```
① REGISTRY 未注册拒绝 → ② surface 确定性授权 → ③ level 分派
→ ④ L2-tool 校验参数并执行 handler / L2-skill 取正文 → ⑤ 输出截断
```

`:35 load()` 是旧 `invoke()` 的替代。关键分支：`level==1` 只返回 schema/manifest 不执行；
`level==2` 按 `kind` 分派 tool（真执行）或 skill（取正文）。错误分类：业务失败 `retryable=False`，
handler 抛的 `CoreError.infra` 上抛走重试。对照 `test_load_level2_tool_executes_real_handler`。

### 2.2 `loop/decide.py`（97 行）—— 纯函数判定树

`:86` 的 `CALL_TOOL` 分支换成 `LOAD_CAPABILITY`，从 `spec.capability_id/level` 构造 Decision。
其余 8 个分支与优先级不变。**铁律仍是**：只读确定性字段，不调模型、不落盘、不发事件。

### 2.3 `loop/run.py`（344 行）—— 真实执行 + 四道防线

这是本次改动最密集的文件（+133 行），按下面顺序读：

| 位置 | 函数/分支 | 职责 |
|---|---|---|
| `:85` | `apply_capability_result` | 结果回灌；拒绝时带 `denied_reason` 供自纠正 |
| `:111` | `_append_denial_message` | 把拒绝事实注入 **system 消息**，让下一轮模型自纠正（不盲目重试） |
| `:121` | `_store_capability_result` | 结果落 ObjectStore，`tool_result_ref` **只存纯 key、不内联内容** |
| `:129` | `_default_capability_guard` | 内核兜底闸门（白名单/surface/level）；生产由注入覆盖 |
| `:162-164` | L0 装配 | 用 `assemble(state, SlotConfig(capability_catalog=...))` 把目录进上下文 |
| `:215-257` | `LOAD_CAPABILITY` 分支 | 闸门 → 执行 → 落 ref → 计数 → 预算，业务失败转自纠正 |

**四个必看细节**（对应独立 review 的 5 个 Blocker 修复）：
1. L0 目录**真进**发给模型的 messages（不是只在测试里传 `SlotConfig`）——见 `test_m03_l0_wiring.py`。
2. 每次 `LOAD_CAPABILITY` **真过闸门**（注入的 `CapabilityGuard.check` 或内核默认）。
3. 业务拒绝转状态迁移 + 注入 system 消息，**不抛异常**（否则 PEL 无限重投）；infra 才 `raise`。
4. L1/L2 **都计 token**；结果落对象存储，ref 是纯 key，不把不可信文本以 user role 回灌。

---

## 3. 阶段 3 · 依赖边界（`injection.py`，250 行）

新增两样东西，读懂它们就理解了「内核如何不依赖上层服务」：

- `:247 CapabilityGuard` 协议：`check(capability_id, level, surface, count, tokens) -> (bool, reason)`。
  **内核不 import services**，用鸭子协议解耦（符合 M03 分层契约）。
- `:247-249 Deps` 新字段：`capability_guard`（生产必注入）、`object_store`（结果落 ref 用）。

生产装配见 `deps.py:294-295`：`ProductionCapabilityGuard()` + `RedisObjectStore()`。

---

## 4. 阶段 4 · 安全闸门（M06）

### 4.1 `services/guardrails/capability_guard.py`（79 行）—— 五项确定性校验

`:50 check_capability_request()` 依次拒：白名单外 / surface 未授权 / level 非 1-2 /
单轮次数超限 / token 超限。拒绝类判定是**代码规则，不靠提示词**——这是沿用 M06 的核心纪律。

`:24 parse_capability_request()` 把模型结构化输出解析为统一请求（旧 `parse_need_skill` 是特例包装）。

### 4.2 `need_skill.py`（77 行）—— 委托，不重复实现

`check_need_skill()` 现在把旧 `depth=1` 映射为 `level=2`，委托给 `check_capability_request`。
**校验逻辑只有一份**；旧调用方（`validate_chain.py`）零改动继续工作。

### 4.3 `orchestrator/capability_guard_adapter.py`（38 行）—— 桥接

把 M06 校验装成 `CapabilityGuard` 协议，由 `build_deps` 注入 run loop。

---

## 5. 阶段 5 · 装配与能力实现（按需读）

### 5.1 装配链（composition root）

```
deps.py:283  register_capabilities(gateway) + freeze_registry()   ← 启动期注册并冻结
    ↓
dispatcher.py:33  _l0_catalog() = tuple(level0())                 ← 每次任务取目录
    ↓
dispatcher.py:426 Input(capability_catalog=...)                   ← 注入 run()
    ↓
run.py:162  assemble(SlotConfig(capability_catalog=...))          ← 进上下文
```

**这是最容易断的地方**：任何一环缺失，模型就看不到能力，整个渐进披露变成死代码。

### 5.2 `orchestrator/capabilities.py`（207 行）—— 首批 6 个能力

| id | kind | 实现 |
|---|---|---|
| `tool.attachment.image` / `.ocr` / `.video` | tool | 复用 `real_processors` + 视觉模型增强；handler 只收 `source_ref`，不收原文 |
| `tool.code.execute` | tool | Docker 一次性容器执行（`sandbox_runner/executor.py`） |
| `recipe.calendar.month.v3` / `recipe.task.list.v2` | skill | 正文来自 `packages/prompts/recipes/*.md` |

注册表被 `freeze_registry()` 冻结。`include_code_exec` / `include_attachments` 开关可按环境关闭。

### 5.3 其他支撑文件

| 文件 | 行数 | 读什么 |
|---|---|---|
| `orchestrator/recipe_loader.py` | 90 | 规程正文加载；`recipe_id` 正则白名单防路径穿越；缺失即 fail-closed |
| `control_plane/attachments/vision.py` | 58 | 视觉理解：复用 ModelGateway 发多模态消息；无网关时 `degraded=True`，不造假语义 |
| `sandbox_runner/executor.py` | 71 | `docker run --rm -i` 执行；`isolation_note="none"`——**暂无沙箱隔离，禁止生产处理不可信代码** |
| `context/slots.py` | 87 | L0 目录槽位固定在系统契约之后、规程之前（保 prompt caching） |
| `migrations/0008_capability_columns.py` | 49 | 给 M06 两表加 `capability_id`/`level`/`capability_kind` 列（DDL 已落，写入方待补） |

---

## 6. 阶段 6 · 测试即文档（强烈建议）

9 组能力主题测试（另 3 组既有测试被改造：`test_m03_context_budget` / `test_m03_decide` /
`test_m03_tools_guardrails`），按「抽象 → 核心链路 → 装配 → 实现」顺序读：

| 顺序 | 文件 | 覆盖要点 |
|---|---|---|
| 1 | `test_m03_capability.py` | 统一抽象、L0 只摘要、L1 不执行、L2 真执行/取正文、未注册拒绝 |
| 2 | `test_m06_capability_guard.py` | 五项校验（白名单/surface/level/次数/token） |
| 3 | `test_capability_registration.py` | 首批 6 能力注册 + 冻结 + L0 不泄漏 |
| 4 | `test_m03_l0_wiring.py` | **端到端**：断言发给模型的 messages 真含 L0 行（B1 证据） |
| 5 | `test_m03_capability_guard_wiring.py` | 闸门真被调用 + 拒绝转自纠正不死循环（B2/B3 证据） |
| 6 | `test_m03_capability_result_wiring.py` | 结果落 store + ref 纯 key + token 累加（B4/B5 证据） |
| 7 | `test_m07_recipe_loader.py` | 规程扫描/加载/缺失 fail-closed |
| 8 | `test_m05_vision_capability.py` | 视觉接口复用 gateway + mock 降级 |
| 9 | `test_m08_code_exec_capability.py` | Docker 命令构造、退出码、超时归类 infra |

---

## 7. 不变量 → 代码位置速查

| 不变量（ADR-003 §1） | 代码位置 | 验证用例 |
|---|---|---|
| 一个抽象（tool/skill 都是 Capability） | `tools/registry.py:22` | `test_registry_is_unified_capability` |
| 分级披露（L0 只摘要） | `registry.py:80 level0()` / `slots.py` | `test_level0_has_only_brief_fields` |
| LLM 自主选择（静态表只作建议） | `loop/run.py:162` + `dispatcher.py:426` | `test_l0_catalog_reaches_model_messages` |
| 门禁确定性（L2 全过闸） | `guardrails/capability_guard.py:50` + `run.py:215` | `test_capability_guard_injected_is_used` |
| 预算不可绕过（L1/L2 都计 token） | `loop/run.py:247-257` | `test_capability_result_stored_and_tokens_accumulated` |
| 不把不可信原文当指令回喂 | `run.py:121` + `invoke.py` L2 分支 | `test_capability_result_stored_and_tokens_accumulated` |
| 运行时拓扑固定 | `registry.py:63 freeze_registry()` | `test_capability_registration.py` |

---

## 8. 两条实用捷径

- **30 分钟抓主干**：阶段 1（registry）→ 阶段 2（invoke + run 的 LOAD_CAPABILITY 分支）→
  阶段 6 的测试 4-6（三个端到端接线证据）。这三条链路串起来就是整个改造的骨架。
- **讲清「为什么要统一」**：配着 `docs/adr/ADR-003-...md` 的 §1 设计意图与 §13 变更记录读，
  特别注意改造前的两个断点（生产零注册 / 静态预取因果倒置）——代码里已无旧实现，
  ADR §13 是唯一记录旧结论的地方。

---

## 9. 未完成项（读代码时别误以为做完了）

ADR §11.5 登记的未完成项，代码里有明确痕迹：

1. **Docker 执行无沙箱隔离** — `executor.py:17 isolation_note="none"`；禁止生产处理不可信代码。
2. **视觉模型真实端点待环境** — `vision.py` 无网关时 `degraded=True`，测试用 mock。
3. **三表新列 DDL 已落但写入方待补** — 迁移 0008 有列，`NeedSkillLogRow` 尚未写新列。
4. **附件 S3 reader 待装配** — `capabilities.py:50` 默认本地路径 reader。

---

## 10. 如何验证你读懂了

```bash
# 全量测试
.venv/Scripts/python.exe -m pytest -q
# Docker 运行时：428 passed（2026-11-21 验收）。
# Docker 未运行时：378 passed / 50 skipped——集成用例有 skip 守卫，
# 属环境非回归；起依赖用 docker compose -f docker-compose.dev.yml --profile core up -d 后重跑。

# 静态检查
.venv/Scripts/python.exe -m ruff check .
npx pyright --pythonpath .venv/Scripts/python.exe

# 契约一致性
pnpm contracts:check
```

能独立回答这三个问题，就算真正读懂了这次改造：

1. 模型是怎么「看见」能力的？从 `build_deps` 注册到 `model.complete` 的 messages，中间经过哪几站？
2. 模型幻觉出一个未注册的 `capability_id` 时，会发生什么？为什么不会让任务卡死？
3. L1 与 L2 的差别是什么？为什么两者都要计 token、结果不能内联进 `tool_result_ref`？
