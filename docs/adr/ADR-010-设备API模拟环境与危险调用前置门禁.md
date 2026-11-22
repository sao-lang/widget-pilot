# ADR-010：设备 API 模拟环境、危险调用前置门禁与会话修正语义

**状态**: **S0–S3 已实现**（2026-11-22；S0 契约/ADR、S1 设备模拟+沙箱门禁、S2 危险调用前置、S3 会话修正均已落地并有测试证据；S4 全链路真模型/影子期校准待 M20 端点）。用户拍板场景：设备压制频繁调用/频繁渲染，危险接口需前置+沙箱联动，两类会话修正。
**范围**: 在 M08 一次性沙箱与 M06 前置校验之上，①设计一个浏览器内可经 API 互通的设备模拟环境（参考 Android AppWidget/Glance 的真实调用约束），检测 widget 对设备 API 的频繁调用、频繁渲染、重试与大任务对性能/续航的影响，命中即判失败并要求重构；②前置校验新增危险接口/高危设备 API/危险动作拦截（词表 + 语义采样表确定性拦截，必要时便宜模型兜底）；③实现两类会话修正语义——违法输入撤销后合并、任务中途改需求重跑。
**关联**: `ADR-004` §3 后续阶段（设备 API/续航/沙箱校验三项）、`ADR-005`（进护栏）、`ADR-009`（多轮任务链）、`docs/modules/M08`（沙箱与出护栏）、`docs/modules/M06`（前置校验）、`docs/modules/M15`（reasoning 通道）
**变更类型**: 新增设备域（生产代码 + 前端桥）、扩展 M06 前置校验、扩展 ADR-009 会话语义。**不新增 ReasonCode**（复用现有枚举，见 §6）。

---

## §1 现状与依据（为什么要做）

用户原始场景（2026-11-22 拍板）：

> 设备可能会限制 widget 对设备 API 的频繁调用以及对 widget 的频繁渲染的压制，因为这些可能引起设备性能、续航的降低。需要设计一个模拟环境，可以在浏览器中通过 API 互通，在 API 中设计一些不能频繁调用的 API，在沙箱里面检测这些 API 以及 TS/js 侧不能频繁渲染、重试、引起性能/续航下降的大任务是否会影响模拟环境的性能，如果影响到的话需要重构代码。API 设计参考手机 widget 和设备真实 API 的交互和调用。
> 前置校验（注入/PII/敏感词）还要校验危险接口、高危 API 调用、其他危险动作；危险接口可能需要沙箱机制配合，高危 API（模拟环境设计的 API）、其他危险动作可能需要词表 + 语义采样表拦截，甚至便宜模型兜底。
> 会话场景：①用户最初输入违法，任务没做下去，用户在同会话说"不进行违法动作"，此时合并两次 message、删除最初输入中的违法动作；②用户开始一个任务，任务进行中暂停、修改需求，此时调整需求重新任务。

**权威调研依据（Android 官方文档，2026-11-22 检索）**：

| 约束 | 具体值 | 来源 |
|---|---|---|
| `updatePeriodMillis` 周期性更新下限 | **30 分钟**（低于 30 分钟系统不接受；设 0 关闭周期更新改用 WorkManager） | developer.android.com/develop/ui/views/appwidgets/advanced |
| 广播接收器（含 `onUpdate`）响应上限 | **10 秒**后判 ANR；耗时操作须 `goAsync`/WorkManager | 同上 |
| 更新类型开销 | 全量 `updateAppWidget` > 局部 `partiallyUpdateAppWidget` > 集合数据刷新 `notifyAppWidgetViewDataChanged` | 同上 |
| Glance 推荐更新频率 | **每小时不超过 1 次**省电 | developer.android.com/develop/ui/compose/glance |
| RemoteViews 位图内存上限 | `6 × width × height` bytes（Android 17+ 抛 `IllegalArgumentException`） | 同上 |
| 后台/Doze 压制 | 屏灭静止后限制网络访问、延迟 jobs/alarms/sync；维护窗口（maintenance window）批量放行；App Standby 五桶 Active/Working set/Frequent/Rare/Never | developer.android.com/training/monitoring-device-state/doze-standby 及 Android 版本说明 |
| WorkManager 约束 | 可按电量/网络/存储条件延迟任务；`addContentUriTrigger` 内容变更触发 | 同上 |

**结论**：真实手机系统对 widget 的资源消耗有**硬性、可量化**的压制。当前 WidgetPilot 的 M08 沙箱只做"编译/渲染/尺寸/运行时/能力/交互"一次性断言，**没有任何设备 API 调用频率、渲染频率、重试风暴、大任务耗时的检测**——这是 ADR-004 §3 遗留缺口的核心。

---

## §2 设备模拟环境设计（DeviceSim）

### §2.1 形态：浏览器内 API + 沙箱桥互通

- 模拟环境是**浏览器内一段受控运行时**（`apps/web` 侧或独立 Vite 页），通过 `window.widgetDevice` 暴露设备 API；生成的 widget 在受限 iframe（M09 运行时）中经 bridge 调用。
- 沙箱（M08）与模拟环境共用**同一份 API 定义**（`packages/contracts` 新增 `device-apis.json` 单一事实来源），保证"沙箱检测到的调用"与"浏览器实际发生的调用"语义一致。
- 不接真实设备系统 API（M08 §2.4 #3 铁律不变）；模拟环境确定性地模型化压制行为。

### §2.2 受限 API 清单（初版，按 Android 约束建模）

| API | 模拟约束 | 依据 |
|---|---|---|
| `device.getLocation()` | 最小间隔 30s；Doze 态拒绝并排队到维护窗口 | 后台定位限流 |
| `device.getBattery()` | 最小间隔 60s；超频返回 `RATE_LIMITED` | 电量查询开销 |
| `device.getNetworkStatus()` | 最小间隔 15s | 网络状态查询 |
| `device.sendNotification()` | 每 widget 每小时 ≤ 5 次 | 通知防打扰 |
| `device.scheduleRefresh(ms)` | `ms < 30*60*1000` 直接拒绝；改走 WorkManager 语义 | `updatePeriodMillis` 下限 |
| `device.render.update()` | 全量更新有成本计数；1 分钟内 > N 次判渲染风暴 | 全量 vs 局部更新开销 |
| `device.storage.read/write()` | 单次 ≤ 配额；频率计数 | RemoteViews 内存上限类比 |

每个 API 返回结构化结果：`{ok, value?, code?}`，`code ∈ {RATE_LIMITED, DOZE_DEFERRED, QUOTA_EXCEEDED, DENIED}`。

### §2.3 压制与性能检测（沙箱内确定性断言）

沙箱 runner 在 widget 执行期间注入 mock `window.widgetDevice`，记录：
1. **调用序列**：每个 API 的调用时刻、参数、返回码（不记敏感值）。
2. **渲染计数**：`render.update()` 全量更新次数、局部更新次数、间隔。
3. **重试检测**：同一 API 在 `RATE_LIMITED` 后 N 次内重复调用 → 重试风暴。
4. **大任务检测**：单次同步任务 > 10s 墙钟（对应 ANR 阈值）或分配大对象（位图内存模型）→ 记录。

**门禁判据**（任一命中即 `SANDBOX_TEST_FAILED` / 新 reason 复用现有码）：
- 调用任何受限 API 超频（≥2 次违反最小间隔）；
- `scheduleRefresh` 请求 < 30 分钟；
- 1 分钟内全量渲染 > 阈值（默认 10）；
- 限流后 3 次内重试；
- 单次任务 > 10s ANR 阈值。

命中时证据包写明"哪个 API、第几次、违反哪条约束、应如何重构"（如"改用局部更新/WorkManager/缓存"）。**这是检测门禁，不自动生成修复代码**。

---

## §3 前置校验扩展（危险接口/高危 API/危险动作）

### §3.1 三层拦截（确定性优先）

1. **词表层**（零模型，进护栏）：危险接口名/高危 API 名/危险动作模式词表，命中即拒。复用 ADR-005 `sensitive.py` + Aho-Corasick 机制，新增独立词表文件（与涉政词库同样按类别由合规方覆盖，代码不内置具体危险词样本）。
2. **语义采样表层**（零模型）：对模型输出 TSX/JS 做 AST 级检测（复用 `static_scan.py`）——调用未声明的 `window.widgetDevice.*`、动态构造 API 名（`device[name]()`）、混淆字符串拼接调用 → 拒。
3. **便宜模型兜底**（可选，生产缺端点 fail-fast）：对前两层未命中但语义可疑的片段做一次廉价分类，仅作为"建议人工复核"，**不得单独作为放行依据**（确定性门禁高于模型自律，ADR-004 铁律）。

### §3.2 与沙箱联动

- 危险接口检测在**进护栏（模型调用前）+ 出护栏（沙箱执行前）**都跑一遍；
- 沙箱中发现的高危 API 调用（§2.3）回写 guard_hit 留证（`task_id` 归因已由 0015 迁移支持）。

---

## §4 会话修正语义

### §4.1 违法输入撤销合并

- 场景：T1 用户输入含违法动作 → V1 拒，会话 `AWAITING_INPUT`，`original_description` 保存原文（ADR-009 R3-1 已有此槽）。
- T2 用户同会话说"不进行违法动作了/改成合法的" → **合并 T1+T2 为一条有效描述**：剥离 T1 违法片段（用词表定位）+ 保留 T2 意图，生成合并后 `description` 再进校验链。
- 合并规则确定性（去违法词表命中片段 + 连接词归一），合并结果写审计留证；合并后仍违法则继续拒。

### §4.2 任务中途改需求

- 场景：任务 RUNNING，用户暂停并改需求 → `POST /api/sessions/{id}/messages` 带新描述。
- 语义：中断当前 run（`interrupt` 幂等，已有），保留 checkpoint/审计历史（不删），以新描述**重新走任务**（新 run_id），旧任务置 CANCELLED。
- 与 ADR-009 `task_turn` 版本链对齐：改需求不覆盖历史 turn，追加新一轮。

---

## §5 不变量（硬门禁）

1. 单任务零回归：无设备 API 调用的 widget 不受影响（空调用序列不触发任何新门禁）。
2. 确定性优先：词表/AST 命中即拒，模型兜底不得单独放行。
3. 沙箱内检测、浏览器内模拟，共用同一 API 契约（单一事实来源）。
4. 会话合并/改需求不删历史（审计 + task_turn 追加）。
5. 不新增 ReasonCode（复用 `GUARD_INPUT_HIT`/`CAPABILITY_DENIED`/`SANDBOX_TEST_FAILED` 等）。
6. 生产代码无测试桩；测试 mock 只在 `tests/support`。

## §6 落地顺序（待用户确认后执行）

- S0 契约 ✅：`device-apis.json` + ADR 本稿（不进四生成物 drift 链，作为独立数据文件）。
- S1 ✅：沙箱 `window.widgetDevice` mock（限流/Doze/配额）+ `device_calls` 上报 + Python 五类违规检测 + `DEVICE_API_ASSERT` 门禁；真容器验证通过。
- S2 ✅：`dangerous_call.py` 词表+形状层 + `check_input` 集成；模型兜底刻意不实现（无端点 fail-fast、不得单独放行）。
- S3 ✅：`revision.py` 撤销合并 + 改需求 interrupt/重跑；18 单测。
- S4 待续：真模型端点下的调用序列/影子期阈值校准（依赖 M20）。

## §7 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 草案 | 2026-11-22 | 首次提出，待用户确认落地顺序与 API 清单 |
| v1.0（本轮） | 2026-11-22 | S0–S3 全落地：device-apis.json、沙箱设备 mock+检测、危险调用前置门禁、两类会话修正；全量 673 passed，ruff/pyright 全绿；commit 4250377(a 快照)/S1/4f32c89(S2)/6388597(S3) |
