# ADR-013：确定性 widget 生成通道（设计已批准 · 合并版）

**状态**: **设计已批准 / 定稿**（2026-11-25；用户授权 AI 拍板 U-1～U-14，后续 U-1～U-4/U-17/U-18 亦拍板）。**本轮只出设计与样本库，未改码**。
**范围**: 新增一条与 LLM 通道**并行**的确定性生成链路：从用户描述（含附件）解析出结构化成分 → 直接生成 WidgetSpec/TSX → 过真三门禁 → 真成包；失败按受控返工处理。覆盖单/多 widget、新建/修改、附件使用。
**关联**: ADR-004（去类型化）、ADR-006（PlanLLM 协议/fan-in）、ADR-014（33 能力目录，独立能力层）、ADR-003（渐进披露）、M05 附件、M06 校验链、M08/M09 门禁成包。
**变更类型**: 新增生成路径 + 理解层 + 编排接线 + 装配层设计变更（不改公共 JSON Schema）。
**一句话结论**: 描述的变化是**封闭成分维度的组合**；一次成分解析多处消费（校验/追问/工具/生成），默认零云端 LLM；门禁失败按四类受控返工，统计口径分开。

> **合并说明（2026-11-25 16:14）**：本 ADR 原为 v1「确定性通道」，后因同一需求连续追加 v2（schema 编译器）、v3（本地小模型解释器）、成分类型学（样本库）、门禁返工五次增量，散成 ADR-013/015/016/017/018 五份。**本版把五次迭代合并为一份完整设计**，保留演进历史于 §10；ADR-015/016/017/018 降级为历史指针（一行指向本文件），ADR-014 是独立能力层保持不动。

---

## §1 背景与诉求演进（一条主线）

用户 2026-11-25 的连续诉求，本质是同一件事：**要一条真实不走 LLM 的 widget 生成链路，且能处理真实复杂输入**：

1. 「有没有精准预测、不使用 LLM 的链路？现在只是 LLM 的挡位分流器」→ v1：确定性通道（N=1 模板 + N>1 确定性 PlanLLM）
2. 「复杂一点就命中不了，设计太薄」→ v2：schema 驱动的槽位编译器（不是加词表）
3. 「0 LLM 路由真的能命中复杂描述吗？我担忧」→ 实测：24 条复杂描述，切片法 21%、全局抽取假阳性 75%；「加本地小模型兜底，重设计解释器」→ v3：三层解释器
4. 「工具函数不止这几个……视频第 xx 秒帧做背景」→ 能力目录驱动（REGISTRY 33 能力 + 渐进披露）
5. 「修改 widget 现在能解析吗？TSX 怎么按描述生成？每次新点补模板不行」→ 类型学：九维封闭成分 + 66 条样本库，明确否决模板库
6. 「门禁失败要返工，不能走 LLM 怎么办」→ 受控返工 + 口径纠偏（0 LLM = 默认路径，不是全局禁令）

**用户最终定性**：「每次都能发现问题，设计的出发点错了」——错在「假设下一层存在」逐层堆设计，而非端到端验证。**本合并版按端到端链路重排，并把未落地部分诚实标注**（§8）。

## §2 现状证据（源码核实）

| 能力 | 现状 | 证据 |
|---|---|---|
| 意图识别 | L1 恒零模型；L2/L3/L4 可选端口生产不注入，只做挡位不生成 | `intent_cascade.py:53-73,168-186` |
| 唯一模型调用点 | `run.py:280`；`decide.py:95-97` 默认 CALL_MODEL | 逐行核对 |
| 多 widget 规划 | PlanLLM 是 Protocol，`run_fanout_planning(facts, llm)` 接受注入 | `ports.py:96-103`；`fanout.py:111` |
| WidgetSpec 生产构造 | ❌ 无构造点 | `run.py:46-50` 白名单不含 spec |
| 三门禁 | validate_and_test / assemble 只有测试调用点，生产零调用 | `entry.py:290`；`bundles/service.py:131` |
| 门禁保守判定 | total passed + 三子门禁各自 passed，缺 sandbox_test 视为未过 | `bundles/adapter.py:17-31` |
| 意图枚举 | 只有 create 侧三值，无修改/删除 | `types.py:38-43` |
| 33 能力目录 | 已注册：14 媒体 + 7 CSV + 6 视觉 + legacy，三级渐进披露 | `capabilities.py:486-746`；`registry.py:80-115` |
| 附件处理器 | 四类纯代码（visual/ocr/video/data_source）+ media_processors 12 函数，全真实现 | `real_processors.py:38/92/132/212` |
| 本地模型 | onnxruntime 1.32 在依赖，无 BIO 权重；默认 bigram 相似度 0.40–0.59 不达标 | 实测 9 条描述 |
| 设备 | Ryzen 7 4800H / 31.4GB，跑小型 BIO 绰绰有余；podman 6.2 + crun | 实测 2026-11-25 |

**结论**：链路是「挡位分流器 + 单一 LLM 出口」，没有真实零 LLM 生成路径；理解层/生成层/门禁串接/返工/模型端点五项均未完成（§8 诚实清单）。

## §3 核心架构决策（端到端一条线）

### 3.1 描述 → 成分（理解层，一次解析多处消费）

- **九维封闭成分**（ADR-017）：`intent`(5) × `operation`(8) × `target`(7) × `host` × `grid` × `data` × `style` × `attach_role`(7) × `ambiguity`(5)。
- **描述千变万化 = 封闭维度组合**：新增说法只加样本，不加代码分支；「4x4/四行四列/宽4格高4格」= 同一 `grid=4x4`。
- **样本库**：`docs/samples/widget-description-corpus.json` 66 条（新建 41/修改 16/附件使用 23），契约测试 `test_v3_corpus.py` 保证维度封闭与覆盖度。
- **位置**：护栏后、语义层前的**前置结构化层**；产出 `DescriptionComponents`/`SlotFill`，语义层只消费不重抽；删除 step11 双意图（原 `classify_intent`），route_layer 增 `COMPILER`/`INTERPRETER`。

### 3.2 三层解释器（覆盖混乱描述）

`RuleInterpreter → LocalModelInterpreter → CloudFallback`（Snips 确定性优先共识）：

- **规则层**：全局槽位抽取 + 共现消歧（「桌面+卡片」=1，主屏幕最长优先）、分散槽位聚合、后置 grid 分配、data 最近宿主归属；实测 24 条复杂描述直接编译 19/24=79%（实例数经消歧修正），追问 5，降级 0。
- **本地层**：onnxruntime + BIO 序列标注权重（U-1），权重走 podman 只读卷挂载 `infra/models/slot-tagger/<version>/`（U-2），缺权重 fail-fast 告警，**禁止 bigram 冒充**（实测相似度不达标）；**本地小模型不算 LLM**（U-3 用户明确）。
- **云端兜底**：`validation.ok=True` + `generation_path=cloud-fallback` → 现有 `run()`→`CALL_MODEL`；可补槽位先追问不降级（U-4 阈值规则≥0.8/模型≥0.75 按实测调）。
- **安全**：拒绝类仍由 L1 最前短路；解释器不产拒绝类；护栏始终在理解层之前。

### 3.3 附件：按描述驱动 33 能力工具链

- 能力目录**从 REGISTRY 派生**（与 LLM tools 同源，L0 摘要→L1 schema→L2 执行），不是自造函数表。
- 描述 → **多步工具链**：「视频第 3.5 秒帧做背景」→ `tool.video.frame_at(at_seconds=3.5)`；「第 5 秒帧裁中间」→ `frame_at → crop(source_from=prev)`；CSV 列筛选 → `csv.filter`。
- 执行走 `Decision(kind=LOAD_CAPABILITY, level=2, surface=generation)` → `invoke.load()` 同一入口；按附件 kind 过滤工具族；派生 ref（如 `frame_at_3.500.webp`）进槽位。
- **真 ffmpeg 6s 视频端到端验证通过**；修既有 bug：`capabilities.py` frame_at 误传 `format`（签名是 `fmt`）。
- **未做**：派生 ref 写进 TSX 背景的生成侧能力、视觉 locate/understand 无真实端点。

### 3.4 生成与门禁（一视同仁）

- N=1 模板编译直出 WidgetSpec/TSX；N>1 用确定性 `PlanLLM` 拆分后逐条编译，复用 ADR-006 的 PlanValidator/fan-out/fan-in。
- **否决模板库**：明确不做「按说法建模板」——TSX 走 `成分 → WidgetSpec（16 必填字段，公共契约）→ 白名单组件封闭渲染`。
- 产物过与 LLM 产物**相同的三门禁**（输出护栏/manifest 契约/真沙箱），DONE 必带 `assemble()` 真 `bundle_ref`；共享 `_finalize_process` 保证 ack/fan-in 不漏。
- `widget_id=uuid5(namespace, spec_hash)` 可复现；tokens=0 但 `sandbox_minutes`/`tool_calls` 照记。

### 3.5 修改/删除意图（本需求的新增能力）

- 扩 `IntentCategory` 支持 modify/delete（内部类型，非公共 JSON Schema）。
- `target` 维度提供 last/ordinal/by_host/by_content/all 定位，依赖会话内 widget 实例注册表（复用 M09 bundle 列表）。
- `operation` 决定改哪个槽位；改后**重跑三门禁 + 重新成包**（新版本），不原地改旧 bundle。

### 3.6 门禁失败 → 四类受控返工

| 失败类型 | 处置 | LLM |
|---|---|---|
| ① 确定性可修（封闭修复表：import 越界/grid 溢出/manifest 缺字段/护栏命中/语法错） | 错误码 → 确定性修复 → 重编译重过门禁 | 否 |
| ② 成分不足（ambiguity） | 回成分层追问/重解析 | 否 |
| ③ 确定性无法修（沙箱运行时逻辑错） | **有界 LLM 返工最多 1 次**，带失败证据重生成→同款三门禁 | 是（例外） |
| ④ 返工仍失败/需实时 API 设备调用 | FAILED + error + audit + 成本账 | — |

③ 硬约束：`generation_path=llm-rework` 留证、`WIDGETPILOT_REWORK_LLM=off` 可关、失败报告按不可信数据处理；不无限重试。

## §4 关键口径（防混淆）

- **「0 LLM」= 默认路径零云端 LLM**，不是全局禁令：理解层规则 + 本地小模型都不算 LLM（U-3）；云端兜底/LLM 返工是显式降级且需标注。
- **统计三口径分开**：一次通过率（真零 LLM）/含确定性修复/含 LLM 返工。此前 79% 是**理解层编译率**，不是门禁通过率，不得当端到端成绩。
- **去类型化不倒退**：类目是用户场景，不是 WidgetType；`ValidationResult.widget_type` 恒 `custom`。
- **开关**：`WIDGETPILOT_DETERMINISTIC=on/off` 默认 on；`WIDGETPILOT_REWORK_LLM` 控制返工例外。

## §5 未决与拒绝项

- **已拍板**：U-1～U-14（v1）、U-1～U-4（v3）、U-4 降级链路、样本库类型学方向。
- **待 S3 决策**：widget 实例注册表落地形态、成分解析器阈值校准、返工修复表扩项。
- **否决留痕**：简化首版、假 ModelGateway、LLM 缓存、替换 LLM 通道、纯规则不接本地模型、bigram 冒充本地模型、按说法建模板库。

## §6 验收口径

- 设计制品：本 ADR + 66 条样本库 + `test_v3_corpus.py`（4 passed）；历史契约三件套见 §10。
- 理解层：24 条复杂描述实测直接编译 79%/追问 5/降级 0；同输入双编译逐字段一致；三反例（桌面+卡片、主屏幕、越界）绿。
- 附件：真 ffmpeg 视频第 3.5 秒帧 → 槽位含 `derived_refs`；CSV 筛选/图片背景用例填对槽位。
- 门禁/返工（S3）：四类路径各有单测；LLM 返工最多 1 次；`WIDGETPILOT_REWORK_LLM=off` 时零 model.complete；三口径分开记录。
- 全局：真 podman 三门禁 + PG bundle + 终态；pyright 0 errors / ruff 绿 / contracts drift=[]。

## §7 覆盖声明

- **覆盖 ADR-006 §6.7**：显式注入的确定性 PlanLLM 是用户拍板主路径，不是「无端点静默降级」；GatewayPlanLLM 语义不变。
- **有条件覆盖 ADR-004 §11**：新增 RouteLayer 枚举值（COMPILER/INTERPRETER），硬样本层仍记 L1/L2/L3。
- **不覆盖**：设备 API 真实调用、实时外部数据、像素级分割、公共 JSON Schema 变更、run()/decide()/Deps.model 模型分支。

## §8 诚实缺口清单（端到端不可用）

| 层 | 状态 |
|---|---|
| 理解层 | 成分体系/样本库已设计，成分解析器 S3 未实现；修改/删除枚举未扩 |
| 生成层 | 无 TSX 生成器（只有 WidgetSpec 契约 + ADR-013 设计） |
| 门禁层 | 生产未串 M08 三门禁（validate_and_test 无生产调用点） |
| 返工层 | 设计完成（本文 §3.6），代码未实现 |
| 模型层 | 本地 BIO 权重、视觉端点、LLM 端点均无 |

**从描述到真 bundle 的端到端链路当前不能用**——这是事实，不遮掩。设计已定边界与接口，落地仍差一大截。用户「出发点错了」的批评已体现在方法转变：**下一步不再堆设计，选一条纵向链路真正打通**。

## §9 建议的第一步（纵向打通）

从已有真实资产出发的最短路径：两个 recipe（`recipe.calendar.month.v3`/`recipe.task.list.v2`）是现成的 TSX 生成规程（有 grid/token_cost/结构约束/红线），33 能力是真注册的，M08/M09 门禁与成包有真实现。先打通「成分解析 → 选 recipe → 填参数 → 真门禁 → 真 bundle」的最小闭环，用一条端到端可跑的链路验证设计，而不是继续加设计。

## §10 变更记录（五次迭代合并）

| 阶段 | 原文档 | 核心结论 | 合并到 |
|---|---|---|---|
| v1 确定性通道 | ADR-013 原版 | 双分流、确定性 PlanLLM、三门禁一视同仁、共享 finalizer | §3.4 |
| v2 schema 编译器 | ADR-015 | 一次编译多处消费、删 step11 双意图、统一追问；grid 缺失澄清不回落 | §3.1/§3.2 |
| v3 本地模型解释器 | ADR-016 | 三层解释器、共现消歧、BIO 权重 podman 卷、本地模型不算 LLM | §3.2/§3.3 |
| 成分类型学 | ADR-017 | 九维封闭成分 + 66 条样本库、修改/删除意图、否决模板库 | §3.1/§3.5 |
| 门禁返工 | ADR-018 | 四类受控返工、0 LLM 口径纠偏、三口径统计 | §3.6/§4 |

**历史契约**（保留作证据，不再作为权威）：`.conductor/contracts/2026-11-25/` 下 `f76a7264-确定性widget生成通道.*`（v1）、`f76a7264-意图schema槽位编译器v2.*`、`f76a7264-本地小模型兜底解释器v3.*`。v2/v3 的 FR 细节以本 ADR §3 为准，避免多处矛盾。

## §11 下游交接

设计已批准；样本库与解释器域已有代码（拦截器一处见工作记忆 2026-11-25）；S3 按 §9 最短闭环推进。文档同步清单见历史 contract `Affected` 段，S5 收尾核对。
