# 意图 Schema 驱动的槽位编译器 v2.0 · 设计规格

**状态**: 待确认（用户已拍板理解层选型与产出范围，本规格待最终读完确认后进 S3）
**范围**: 把「意图识别」从二分类挡位器重构为**前置结构化编译层**：声明式意图 schema + 纯规则槽位抽取 + 附件结构化 + 实体归一化 + 缺槽位追问，一次编译产出 `SlotFill`，让复杂描述（文字 + 多附件 + 多 widget）在零 LLM 下精准命中生成。
**技术栈摘要**: Python 3.13 / 声明式 schema（JSON Schema 风格常量）/ 现有附件纯代码处理器 / 零新增依赖 / 零 LLM 调用
**关联**: ADR-015（本设计权威，新建）、ADR-004（去类型化：去的是 WidgetType，本设计去的是「意图二分」）、ADR-006（PlanLLM 协议复用）、ADR-013（v1 被本设计覆盖理解层部分）、M05 附件、M06 校验链、M08/M09 门禁成包
**变更类型**: 架构级补充设计（理解层重构，不改公共契约 schema）

---

## §1 设计意图

- **一句话意图**：让语言理解**只做一次**、产出一份完整结构化结果，所有下游（校验/澄清/规划/生成/成包）消费同一份；复杂描述的命中率由 **schema 覆盖度**决定，不由正则词表数量决定。
- **设计原则**（可判定）：
  1. **一次编译、多处消费**：`SlotFill` 是唯一事实源；语义层禁止再自己抽槽位（`_has_required_slots` 等重复检查删除或改为消费 SlotFill）。
  2. **schema 是事实源，不是词表**：每个意图声明 slot 名/类型/取值域/归一化器/追问话术/来源（text/attachment/selector）；新增场景=加 schema，不是加正则。
  3. **严格零 LLM**：理解层只做确定性规则 + 已存在的纯代码附件处理器（`process_visual/ocr/video/data_source`）；不注入 embedder/classifier/gateway。
  4. **附件即结构化输入**：四类附件解析成 typed slot 值（CSV→行列、图片→主色/OCR/尺寸、视频→关键帧、文本→正文），与文字槽位同权参与编译。
  5. **缺槽位追问统一**：不再三处分散澄清（描述充分性/role/grid），统一为「slot 缺失 → 该 slot 的 dialog 追问」，复用现有 AWAITING_INPUT 状态机。
  6. **安全顺序不变**：注入/PII 护栏仍在理解层之前，编译输出按不可信数据处理。
- **价值排序**：结构化完整性 > 复杂描述命中率 > 零 LLM 严格性 > 与既有链路兼容 > 实现简洁。
- **反意图**：不做 NLU 小模型；不追求「任意自由表达都能命中」；不把理解逻辑散落回校验层；不改 `run()`/`decide()`/`Deps.model`。
- **与既有设计意图的关系**：沿用 ADR-004 去类型化（本设计去除的是「意图二分类 + 类型词表」，不恢复 WidgetType 门禁）；覆盖 ADR-013 v1 的「理解层=正则模板匹配」部分，保留其门禁/成包/终态/finalizer 成果。

## §2 背景与调研证据

**用户三条重定义（原话）**：①专门用于生成手机 widget 的 agent；②输入含文字描述和附件（视频/图片/CSV/文本），可用于 widget 创造；③一次可生成多个 widget。期望「复杂描述中精准命中不走 LLM 的路径，直接根据解析出来的要求生成 widget」。

**调研（4 源，2026-11-25 实测搜索）**：

| 方案 | 做法 | 可借鉴点 |
|---|---|---|
| Google A2UI（2025-12 开源，Opal/Gemini Enterprise 生产） | 纯 JSON 声明式 UI + 组件白名单 + schema 校验；注入攻击拦截 100% | 理解结果落到声明式中间层；执行层刚性白名单 |
| Apple App Intents / App Schemas | 应用声明 schema + 类型化 `@Parameter` + 附件 `supportedContentTypes` + 缺参 dialog + 短语注册 | **schema 声明 + 缺参追问 + 附件类型化**是复杂描述命中的关键 |
| Snips NLU（嵌入式） | 确定性解析器（正则+实体值枚举，训练样本 1.0 F1）优先，概率解析器兜底；独立实体归一化（"tomorrow evening"→时间戳） | **规则优先 + 实体归一化**；本设计只取确定性半边（用户拍板零 LLM） |
| 企业文档抽取 2025 共识 | 规则可审计/可复现/低成本；LLM 处理长尾；OCR 后接业务规则校验 | 附件解析规则优先、低置信才升级 |

**共识**：大厂不做「纯规则读懂任意复杂描述」，而是**让语言理解停在结构化中间层，执行层永远刚性**。本设计据此定位缺层。

**现状证据（源码核实 2026-11-25）**：

| 事实 | 证据 |
|---|---|
| 意图识别被劈两半：L1 越界在最前、完整级联在最后 | `validate_chain.py:190-232` vs `:316-336` |
| 语义层重复抽槽位（描述充分性 + 附件 role + grid 三处各自判） | `semantic.py:84-142`；澄清散在 `INPUT_INVALID`/`SPEC_MISSING`/role 三处 |
| 无 per-intent schema；L1 只有二分类挡位词表 | `constants.py:118-123` `WIDGET_GENERATION_PATTERNS` |
| 附件处理全纯代码、零 LLM | `real_processors.py:38,92,132,212`（visual/ocr/video/data_source）；`role_resolver.py:20-64` 规则表 |
| 附件类型 image/video/text，CSV 走 text/csv | `attachments/constants.py:9`；`validate.py:18` |
| 多 widget 用 PlanLLM 协议，可注入 | `tasks/ports.py:96-103`；`fanout.py:111` |

**动机**：用户指出「太薄」——`{"桌面上加个 2x2 显示今天日期的卡片"}` 这种简单描述好拆，但期望的是复杂描述的精准命中。根因是架构缺「意图 schema + 槽位抽取 + 归一化」中间层，而非词表不够大。

**成功标准**：一条含 2 个 widget + 2 个附件（CSV + 图片）+ 缺 1 个槽位的复杂描述，经过一次编译产出完整 SlotFill，缺槽位触发**一次**统一追问，用户答完后零 LLM 生成两个真 bundle；mock gateway 计数全程 0。

## §3 范围与边界

- **范围内**：意图 schema 定义（widget 生成意图族）、槽位声明（名/类型/取值域/归一化/dialog/来源）、纯规则槽位抽取器、四类附件结构化、实体归一化、SlotFill 数据结构、编译层插入校验链之前、语义层改为消费 SlotFill、统一缺槽位追问、多 widget 拆分消费 SlotFill、编译结果到 WidgetSpec 的桥接；**内部 Python 类型（`types.py` 的 `ValidationResult`/`SemanticResult`、`tasks/ports.py` 的 `PlanningFacts`）新增 slot_fill 字段**——这不是公共 JSON Schema，不触发 SDK 四同步。
- **范围外**：改 `run()`/`decide()`/`Deps.model` 的模型分支；改**公共 JSON Schema**（`widget_spec.schema.json`/`widget_bundle.schema.json`/`events.schema.json`）；引入任何模型（含 embedder/小模型）；设备 API 真实调用与实时外部数据；LLM 通道自身的 M08 门禁串接。
- **前置依赖（红队 5.3）**：本设计的**成功标准依赖 ADR-013 落地**（WidgetSpec 构造器 + 三门禁 + 真成包 + finalizer）。ADR-013 当前「设计已批准、未改码」，故「零 LLM 生成两个真 bundle」为 **S3 后置验收**，S2 只验收理解层本身。
- **反向排除**：不把 widget_type 当 schema 选择键（沿用 ADR-004，恒 `custom`）；不把「未声明 slot」的描述强行编译（→ 追问或回落）；不把附件二进制塞进 spec（只放 `insight_ref`）。

## §4 技术栈

| 类别 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.13 | 仓库一致 |
| schema 表达 | Python dataclass 常量（声明式，字段=slot 定义） | 零新增依赖；可版本化、可进 change_gate 指纹 |
| 槽位抽取 | 规则正则 + 取值域枚举 + 共现窗口（Snips 确定性解析器形态） | 零 LLM、可审计、1.0 确定性 |
| 附件解析 | 复用 `real_processors.py` 四个纯代码处理器 | 已存在、零 LLM、带 hash 与质量分 |
| 状态机 | 复用现有 `AWAITING_INPUT` + `clarification_reply` 重入 | 不新增会话状态 |
| 测试 | pytest + 复杂描述矩阵 + mock gateway 计数 | 证明零调用与命中率 |

## §5 需求清单

### 5.1 Schema 层

- **FR-1**：`IntentSchema` 声明式结构：`intent_id` + `slots: tuple[SlotSpec]`；`SlotSpec` 含 `name`/`type`/`domain`(枚举或取值域)/`required`/`normalizer`/`dialog`/`source ∈ {text,attachment,selector,attachment_role}`。验收：**至少 11 个 widget 意图族**（对齐 ADR-004 §9 的 11 类：信息展示/时间日程/效率工具/健康运动/设备控制/媒体娱乐/生活服务/金融信息/系统状态/通讯社交/静态文案，其中需实时 API 或设备真实调用的类只登记 schema 与「静态骨架 or 拒绝」边界，不伪称实时）各有完整 schema；schema 自校验（无重复 slot、dialog 非空）。**注意**：`context` 来源无输入数据源（红队 4.2），已删除。
- **FR-2**：schema 版本化：`INTENT_SCHEMA_VERSION` 进 `change_gate.ConfigSnapshot` 指纹与 SlotFill 溯源。验收：改 schema → `diff_config` 非空。

### 5.2 编译层（一次编译）

- **FR-3**：`compile_intent(description, attachments, fields) -> SlotFill | NeedClarification`，纯规则、零模型（**红队 2.3：删除 OutOfScope 返回——L1 已在最前短路，编译层收不到越界输入，透传类型是死代码**）。`SlotFill` 含 `intent_id`/`instance_id`/`slots: dict`/`missing_slots`/`evidence`（每个槽位原文或附件 ref + 命中规则 id）。验收：同输入两次编译结果逐字段一致。
- **FR-4**：槽位抽取支持三类来源：①文字（正则 + domain 枚举 + 共现）；②附件（按 role/contentType 分派解析器）；③选择器（grid）。**文本槽位不做裸词放行**——必须有「动作/宿主/数据词」联合锚点。验收：复杂长句里能同时抽出 2 个 widget 的各自槽位。
- **FR-5**：实体归一化：grid（`GRID_HINT_PATTERNS`）、时间/日期、颜色（命名色→hex）、数值、附件引用统一转成 typed 值（参考 Snips Entity Resolution）。验收：「下周一」「大一点的四宫格」等说法归一为确定值或进入 missing。
- **FR-6**（红队 1.1/1.2 补）：附件结构化按 **`role × kind → 处理器 → slot` 映射矩阵**（不是只按 role）：
  - `visual_reference × image/video` → `process_visual` / `process_video().frames` → palette/尺寸/缩略图 slot（style）
  - `content_extraction × image/video/text` → `process_ocr` / `process_video().regions`（视频逐帧 OCR `real_processors.py:183-201`）→ text slot
  - `data_source × text(csv/json)` → `process_data_source` → data slot（严格解析不 eval）
  - `style_reference × image` → `process_visual().palette` → style slot
  - **空数据降级**：CSV `row_count==0`（`real_processors.py:229-242` 返回空 rows 但 quality 仍 1.0）或 `insight_quality` 低于阈值 → 对应 slot 置 missing，走 FR-10 dialog，禁止空数据 widget。
  - 验收：CSV + 图片 + 「视频里提取文字」三类各自填对槽位；空 CSV 进入 missing；gateway 计数 0。
- **FR-7**（红队 1.4 补）：多 widget 拆分：编译层一次识别多个意图实例（每个带 `instance_id` + 自己的 SlotFill），通过 `DeterministicPlanLLM` 注入 `PlanLLM` 协议（ADR-013 FR-3/5 复用，调和结论见 ADR-013 §3.2）落子任务。**SlotFill 持久化**：按 `(session_id, run_id, instance_id)` 存 PG（复用 AuditEvent 快照机制或新轻量表，S3 定），每个实例独立追问状态；一个实例缺槽位只阻塞该实例，**已完成的实例不重复生成**（按 instance_id 幂等）。验收：2 widget + 2 附件、其中一个缺槽位 → 补答后只生成缺失的那个，另一个 bundle 不重复落库。

### 5.3 位置与消费

- **FR-8**：编译层插在**进护栏之后、语义层之前**（`:234-272` 之后、`:274` 之前）——安全注入/PII 仍最前；编译输出按不可信数据对待。验收：注入描述在编译层前被拦，不进编译。
- **FR-9**（红队 4.1 补）：语义层（`validate_semantic`）改为**消费 SlotFill**：描述充分性/附件 role/grid/feature 四项均读 SlotFill 对应字段；`_has_required_slots` 与 `_detect_feature` 的抽取逻辑移入编译层，**feature 声明为编译层产出的 slot**；语义层只读 `SlotFill.feature + SlotFill.grid` 判 `SPEC_CONFLICT` 并回 `suggest_grid`，**不改 slot**——追问后由编译层重编译该实例。`role` 歧义单独保留 `question_kind=role` 与专属 dialog（`_ROLE_HINT`），不混入普通 slot 缺失。验收：语义层不再调用独立抽取函数（grep）；冲突仍能判；role 追问文案不丢。
- **FR-10**：统一缺槽位追问：`NeedClarification` 携带 `missing_slots`/`question_kind ∈ {slot,role}` 与 dialog（取自 schema 或 role hint），复用现有 `AWAITING_INPUT` 状态机与 `clarification_reply` 重入（`:280-297`），重入时只补缺失 slot、不重编译已填槽位。验收：一次复杂描述只追问一次；补答后不触发二次「描述不足」死循环。
- **FR-11**（红队 2.4 补）：**SlotFill 传递载体**：`ValidationResult` / `SemanticResult`（`types.py`）新增 `slot_fill: SlotFill | None`；`PlanningFacts`（`tasks/ports.py:33-50`）新增 `slot_fills: tuple[SlotFill, ...]`；`Input`（`types.py:615-648`）新增 `slot_fills` 供 dispatcher 透传给 run/spec 构造器。以上是**内部 Python 类型**，不是公共 JSON Schema（§3 已澄清边界）。编译结果 → WidgetSpec 映射复用 ADR-013 FR-7 构造器，过 M08 三门禁 + 真成包 + finalizer（ADR-013 FR-11~15）。验收：SlotFill 从编译层一路可追到 spec 构造器，gateway 计数 0（S3 后置）。
- **FR-16**（2026-11-25 14:15 用户澄清后新增：**LLM 降级链路**）：`compile_intent` 返回 `Uncompilable(reason)` 且 reason ∈ {schema 未覆盖, 自由表达, 附件解析不足} 时——**不是拒绝，是降级**：validator 返回 `ok=True` + `ValidationResult.generation_path=llm-fallback`（内部 Python 字段，非公共 schema），dispatcher 按现有 `:775-820` 构造 `Input` 调 `run()` → `CALL_MODEL`，**不新写任何 LLM 路径**。可补槽位的缺失仍走 FR-10 追问，不降级。验收：自由表达描述走 `model.complete` 且 `generation_path=llm-fallback`；规整描述 gateway=0。
- **FR-17**（降级防循环）：LLM 产物过 M08 三门禁；失败 fail-closed 转 FAILED + error + audit，**禁止「LLM 失败再回编译」或无限重试**；降级路径成本照记（`tokens>0`，与零 LLM 路径的 `tokens=0` 区分）。验收：门禁失败用例断言无二次降级、成本账 tokens>0。
- **FR-12**（红队 2.1/2.2/3.1 补，**双意图仲裁**）：**删除 `step11` 的 `classify_intent` 调用**（`validate_chain.py:316-336`），理解权归编译层唯一所有——不存在两个意图结论打架；`ValidationResult` 的 `route_layer` 改为 `RouteLayer.COMPILER`（**新增枚举值，需 ADR 变更明示覆盖 ADR-004 §11 红线**；ADR-004 硬样本在**编译层前**的 L1 处仍记 L1/L2/L3，不受影响）；`intent_normalized` 取编译层 SlotFill 的 intent_id；`model_tier`/`similarity`/`latency_ms`/`fallback_reason` 由编译层产出（零模型时 tier 记 `deterministic`）。**同时删除 `validate_chain` 的 `embedder/classifier/gateway` 三个端口参数与 `intent_cascade` 的 L2/L3/L4 注入**（严格零 LLM——生产装配无模型端口可注入），I14「拒绝类零模型」自然保持。验收：import 图无这三点；grep `classify_intent(` 无生产调用；ADR-004 三条硬样本原样绿。

### 5.4 可观测与红线

- **FR-13**：编译可观测：intent_id / 命中 slot 数 / 缺失 slot 列表 / 附件解析质量 / schema 版本写日志与独立指标（不改事件 schema）。验收：复杂用例日志可查全链路归因。
- **NFR-1**：零模型依赖——理解层不注入 embedder/classifier/gateway；验收：import 图无这三端口，`pyright`/`ruff` 绿。
- **NFR-2**：不新增依赖；不改公共契约 schema；验收：git diff 不含 schema 文件。

## §6 Approaches

| 方案 | 做法 | 优点 | 代价 | 结论 |
|---|---|---|---|---|
| A schema 驱动前置编译（本设计） | 声明式意图 schema + 一次编译 + SlotFill 多处消费 | 复杂描述命中靠 schema；无重复抽取；统一追问；严格零 LLM | 需写 schema 与抽取器；长尾仍需回落 | **采用**（用户拍板） |
| B 继续加正则/词表（v1 路线） | 扩 L1 词表与模板 | 改动小 | 永远追不上复杂描述；三处重复检查；用户已否决「太薄」 | 否决 |
| C 本地小模型做槽位填充 | Snips 概率半边 | 命中率高 | 引入模型依赖，违反用户「严格零 LLM」拍板 | 否决（本轮） |
| D 纯 LLM 理解 | 主模型抽 slot | 最灵活 | 非零 LLM；成本高；与产品定位冲突 | 否决 |

## §7 架构设计

### 7.1 位置与数据流

```
process_one
  → _check_guardrail：
      step0 L1 越界短路（:190-232，I14 零模型）
      → 进护栏 注入/PII（:234-272，安全红线）
      → 【新】compile_intent(description, attachments, fields) → SlotFill
             ├─ OutOfScope → 透传 L1 结论（短路）
             ├─ NeedClarification → missing_slots + dialog → AWAITING_INPUT
             └─ SlotFill（完整）
      → validate_semantic 消费 SlotFill（不重抽）：
            缺 slot 按 dialog 追问（统一）；冲突 → SPEC_CONFLICT
      → step11 原级联仅记 route_layer / 放行方向（不再做理解）
  → 多 widget：DeterministicPlanLLM 消费多个 SlotFill 拆子任务
  → 每子任务：SlotFill → WidgetSpec → 三门禁 → assemble → finalizer
```

### 7.2 接线四问
1. **谁调用**：`validate_chain` 在护栏后调 `compile_intent`；`_plan_and_fanout` 调 `DeterministicPlanLLM`（消费 SlotFill）。
2. **它调用谁**：附件处理器（四个纯代码函数）、schema 常量、`uuid5` 归一化；无模型端口。
3. **信号谁消费**：SlotFill 被语义层、规划器、spec 构造器、日志共同消费；`missing_slots` 被追问消费。
4. **闭环**：描述+附件 → SlotFill → (追问补全) → WidgetSpec → TSX → 门禁 → bundle → 终态，每段字段有下游消费者。

### 7.3 装配入口
`worker.main()` 构造 `IntentSchemaRegistry`（默认全意图 schema）并注入 validator；schema 缺失 fail-fast，禁止空 registry 静默放行。理解层不接任何模型端口。

## §8 其他关键维度

- **数据与存储**：SlotFill 不单独落表（复用现有输入快照 audit + routing 日志携带归因），避免新迁移；附件 ref 仍走 S3/PG。
- **错误降级**：可补 slot 缺失 → 追问；编译能力不足（schema 未覆盖/自由表达）→ 降级 LLM（FR-16）；恶意输入 → 护栏前置拦截。
- **兼容**：公共契约 schema 不变；`INTENT_SAMPLES_VERSION` 不变（Prototype 不改 L1 统计）；ADR-004 硬样本不变。
- **可观测**：见 FR-13；尤其记录「附件解析质量分」以便评估是否需要扩 schema。

## §9 样例之外覆盖

| 补全项 | 维度 | 来源 | 状态 |
|---|---|---|---|
| 单 widget 简单描述 | 基线 | ADR-013 | 已覆盖（schema 化后仍命中） |
| 2 widget + 2 附件 + 缺 1 槽位 | 复合复杂 | 用户本轮原话 | 已纳入成功标准 |
| CSV/图片/视频/文本四类 | 同类枚举 | `real_processors.py` | 已纳入 FR-6 |
| 无 grid | 边界值 | SPEC_MISSING | 统一为 slot 缺失追问（FR-10） |
| 附件 role 歧义 | 边界值 | `role_resolver.py:41` | 统一为 slot 缺失追问（FR-9/10） |
| 长尾口语「能显示日期的小玩意」 | 反向 | ADR-004 硬样本 | schema 槽位锚定命中，route_layer 不变（FR-12） |
| 需实时 API / 设备调用 / AI 摘要 | 反向排除 | ADR-004/010 | schema 未覆盖 → 追问或回落，不伪造 |
| 注入/PII 描述 | 异常 | 进护栏 | 编译前拦截（FR-8） |

## §10 未决假设清单

| # | 未决 | 当前结论 | 状态 | 决策人 |
|---|---|---|---|---|
| U-1 | 理解层选型 | 纯规则 + 完整 schema + 附件解析（严格零 LLM） | **用户已拍板** | 用户 14:00 |
| U-2 | 本轮产出 | 重写设计 v2.0 + ADR-015 | **用户已拍板** | 用户 14:00 |
| U-3 | 理解层位置 | 护栏后、语义层前，一次编译多处消费 | **用户已确认** | 用户 14:03「可以」 |
| U-4 | schema 未覆盖的意图 | **降级到 LLM 通道**（2026-11-25 14:15 用户澄清：严格零 LLM 只约束理解层，不等于系统永不调 LLM）；见 FR-16 | 已修正 | 用户 14:15 |
| U-17 | 降级入口与防循环 | `validation.ok=True` + `generation_path=llm-fallback` → 现有 `run()`→`CALL_MODEL`（`dispatcher.py:775-820`）；产物门禁失败 fail-closed，禁二次降级/无限重试（FR-16/17） | 待确认 | 用户 |
| U-5 | 意图族覆盖度 | **11 族对齐 ADR-004 §9**（FR-1 已改）；首版量化门槛见 §12；按数据反馈扩 schema | 已修正，待确认 | 用户 |

## §11 约束与红线

- 零 LLM 严格：理解层不得注入任何模型端口，由 import 图与 gateway 计数双重证明。
- 不改公共契约 schema；不改 `run()`/`decide()`/`Deps.model`；不恢复 WidgetType 门禁。
- 安全护栏（注入/PII）必须在编译层之前，编译输出按不可信数据处理。
- ADR-004 三条硬样本与 route_layer 统计不变；拒绝类零模型（I14）。
- 不写 fake/stub 冒充 adapter；真实环境用 podman。

## §12 验收标准

| 要求 | 验证方式 | 达标口径 |
|---|---|---|
| FR-1/2 | schema 自校验 + change_gate 指纹 | **11 族齐全**、diff_config 非空 |
| FR-3/4/5 | 复杂描述矩阵单测（同输入双编译） | 逐字段一致、槽位锚定不裸放 |
| **命中率门槛（红队 5.2）** | 固定样例集测试：≥20 条复杂描述（含多 widget、多附件、口语化长句、缺槽位），统计成功编译率 | **基线实测（2026-11-25，24 条复杂描述，纯规则词表探针）：直接编译 18/24=75%、追问后 22/24=92%、降级/拒绝 2/24；目标设为直接编译 ≥70%、含追问 ≥85%**。注意：探针的 widget 实例数用「宿主词计数」估算，存在假阳性（「桌面+卡片」同一对象被数成 2），实例数需 v2 实现时用位置共现消歧后重测。样例集写入测试文件可复核。 |
| FR-6 | CSV+图片+视频+文本附件解析用例 | 填对槽位、gateway=0 |
| FR-7 | 2 widget + 2 附件 fan-out 用例 | 两子任务各自成包 |
| FR-8/9/10 | 顺序断言 + 语义层 grep + 一次追问用例 | 护栏在前、无重复抽取、无死循环 |
| FR-11/12 | 真 podman 门禁 + ADR-004 硬样本 | bundle 真、硬样本绿 |
| FR-13 | 日志/指标断言 | 全链路归因可查 |
| NFR | import 图 + pyright + ruff + contracts:check | 零模型依赖、全绿、drift=[] |

## §13 变更记录

| 变更点 | 原结论 | 新结论 | 理由 |
|---|---|---|---|
| 理解层形态 | v1：L1 二分类挡位 + 正则模板 | v2：schema 驱动槽位编译器 + SlotFill | 用户「太薄」+ 大厂调研 |
| 理解层位置 | 劈两半（step0 + step11） | 前置一次编译，语义层消费 | 用户问位置 + 消灭重复抽取 |
| 澄清机制 | 三处分散（描述/role/grid） | 统一 slot dialog 追问 | Apple App Schemas 借鉴 |
| 附件 | v1.1 可确定性填 attachments | v2 作为结构化槽位源参与编译 | 四类处理器全纯代码 |

## §14 下游交接

- 用户读完确认 → 进 change-contract 固化 → S3 TDD。本轮只出设计，不改码。
- ADR-015 为设计权威；本规格与 ADR-015 同步落盘；ADR-013 保留为 v1 历史，理解层部分以 ADR-015 为准。
