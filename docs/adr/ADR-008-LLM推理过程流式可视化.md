# ADR-008：LLM 推理过程的受控流式可视化（设计已批准 · v0.3 扩展历史回看）

**状态**: **设计已批准**（2026-11-22 O1–O6；2026-11-23 v0.3 扩展历史加载）/ **实现未开始**（S0 契约变更与改码尚未执行；契约侧仅有简化的 `reasoning` kind 前置）
**范围**: 在既有 SSE 类型化事件总线上新增**受控**的 `reasoning` 通道；展示 provider 提供的推理摘要与 harness 产出的确定性过程解释；服务端 SSE 从「有限窗口 + 重连」升级为「窗口回放 + 实时 tail」；**v0.3 扩展**：进会话页展示最新思考、上滑分页加载历史思考（参考 Claude / Cursor / WorkBuddy 的分段折叠 + 游标分页模式）。不改 `plan` 语义，不展示原始思维链。
**关联**: `设计方案.md` §6.4（不做 token 刷屏）、`docs/modules/M15-分通道流式渲染.md` §1.6/§4.2/§9.5、`docs/modules/M17-时间线回放与状态管理.md` §6.6（event-sourced reducer / scrubber）、`docs/modules/M20-LLMProvider模型接入与切换.md` §0.3/§4.1、`docs/modules/00-总览与模块地图.md` §1.6 D-5 / §9.1 Q6；上游依赖 M02 契约门禁、M03 `Delta`/EventBus、M04 SSE 端点、M20 ProviderAdapter。
**变更类型**: 契约 + 模型层 + 传输层 + 前端呈现 + **历史事件持久化**的设计变更。**已批准（2026-11-22）**，有条件覆盖「不做 token 刷屏」与「`plan` 不是思维链」两项旧表述：仅允许在**独立 `reasoning` 通道**里展示受控摘要，不把 `plan` 改成思维链、不把 content 改成逐 token 打字机。D-5「不用 Agent 框架」始终有效。
**一句话结论**: 新增第 19 类事件 `reasoning`，只承载「推理摘要 + 确定性解释」，严禁原始 CoT；SSE 用 Redis Streams `XREAD BLOCK` 做实时 tail；**进页面 REST 拉最近一屏 + SSE 续 tail，上滑按游标向前分页拉历史（事件落 PG 支撑）**；前端默认折叠展示，旧客户端按未知 kind 忽略即可。

---

## §1 现状证据（为什么不能直接接）

| 层 | 现状 | 证据 |
|---|---|---|
| 事件契约 | 18 类 `AgentEventKind`，payload 无 `token` / `delta` / `thinking` / `reasoning` / `status` 分支；`plan` 被定义为结论式短句 | `packages/contracts/events.schema.json:14-17,24-37`；`packages/contracts/enums.source.json:11` |
| 事件传输 | 事件写 Redis Streams（`XADD`/`XTRIM`），SSE 只拉窗口 + 一条心跳后关闭，靠 `Last-Event-ID` 重连；**无实时订阅** | `services/orchestrator/adapters.py:433-503`；`services/control_plane/sessions/sse.py:27-85`；`services/control_plane/api/server.py:456-490` |
| 模型层 | `complete()` 虽传 `stream=True`，但按整份 JSON 解析，只取 `choices[0].message.content`；`ModelOutput` 无 reasoning 字段 | `packages/agent_core/model/provider.py:62-113`；`packages/agent_core/types.py:663-676` |
| 增量解析 | `StreamDecoder` 只解析 `delta.content`，`Delta` 只有 `text`/`done`，且仅测试引用，生产未接 | `packages/agent_core/model/stream.py:12-77`；`packages/agent_core/types.py:679-686` |
| Responses API | 推理项只设计不实现，无 wire adapter | `docs/modules/M20-LLMProvider模型接入与切换.md:44,1014` |
| 前端 | 只有健康页骨架，无 `EventSource` / `ReadableStream` 消费代码；M15 规划文件尚未落地 | `apps/web/src/App.tsx:5-24`；`docs/modules/M15-分通道流式渲染.md:201-237` |
| 既有缺口登记 | M15 已登记「缺异步订阅新事件的推送机制」「前端无真实事件流来源」 | `docs/modules/M15-分通道流式渲染.md:1091-1101` |

结论：这不是一个开关，也不是单纯把 `stream=True` 接上；它跨越契约、模型、传输、前端四层。

---

## §2 目标、非目标与覆盖声明

### 2.1 目标

1. 用户能看到**有边界的推理过程**：模型侧推理摘要（若 provider 提供）+ 系统侧确定性阶段解释。
2. 增量可达，但不是 token 打字机：按**段（segment）**推送，前端按段缓冲与折叠。
3. 复用现有事件总线、`seq` 去重、`Last-Event-ID` 续传、心跳与 trace；不另起一套通道。
4. provider 无关：支持摘要的 adapter 产出 L1，不支持的只产出 L2；缺配置不静默降级为假摘要。
5. 推理内容不进入提示词、路由判据、终态裁决或审计证据。

### 2.2 非目标

- **不展示原始 chain-of-thought / `reasoning_content` 全文**，不把原始推理 token 写进事件、PG、对象存储、日志、遥测或 `ModelOutput.raw`。
- 不把 content 改成逐 token 打字机；不把 `plan` 改成思维链。
- 不新增 `SessionState`、不新增 `ReasonCode`、不引入 Agent 框架或 LLM 专用前端 SDK。
- 不让前端根据推理文本重判护栏、规格或终态。
- 不把推理摘要作为评测判据或产物可信证明；证据仍以 `error.evidenceRef` / 校验 / 沙箱结果为准。

### 2.3 覆盖声明

| 旧表述 | 本 ADR 的处理 | 是否撤销原文 |
|---|---|---|
| §6.4「不做 token 刷屏」 | 保留。新增的是**按段推送的受控摘要通道**，不是 token 流 | 否，仅限定适用范围 |
| `plan` = 结论式短句，不是思维链 | 保留。`reasoning` 是独立 kind，`plan` payload 不变 | 否 |
| §9.1 Q6「18 种 kind」 | 设计已批准新增 `reasoning`（设计态 19 种）；S0 落地并经 M02 门禁后替换计数 | 是（数值更新，走 ADR 留痕；未改码前验收仍按 18） |
| M15 §1.6「不做 token 刷屏」 | 增加例外说明：未知 kind 忽略的旧客户端不受影响 | 否，补充 |

---

## §3 概念模型：三层推理信息

| 层 | 名称 | 来源 | 是否可展示 | 是否可落库/回放 | 处理规则 |
|---|---|---|---|---|---|
| L0 | 原始推理 token / CoT | provider 原始响应 | **禁止** | **禁止** | 在 adapter 边界即丢弃；从 `ModelOutput.raw` 中剥离；不进日志与遥测 |
| L1 | 推理摘要 | provider 明确标记为 summary 的字段/事件（如 Responses 的摘要 delta） | 可，默认折叠 | 仅 Redis 窗口内；跨天快照不重建 | 过护栏与敏感过滤；限长；`source=PROVIDER_SUMMARY` |
| L2 | 确定性过程解释 | harness 按状态迁移产出（如「规格冲突，已询问用户」「回边 1/2 修正代码」） | 可，默认可见 | 可随快照重建 | 零 LLM；只含结论式短句与枚举；`source=HARNESS_DETERMINISTIC` |

**为什么不是只做 L2**：用户想看「模型为什么这么做」，L2 只能解释系统做了什么；L1 在 provider 支持时提供模型侧依据。
**为什么不做 L0**：原始 CoT 可能含敏感信息、提示注入片段、供应商知识产权，且不代表真实决策依据；本项目一贯以确定性门禁为可信来源。

---

## §4 契约设计（M02）

### 4.1 新增事件 kind

`AgentEventKind` 从 18 增至 19，追加 `"reasoning"`：

```json
["run", "plan", "spec", "attachment", "routing", "code", "guard", "validation", "sandbox", "artifact", "cost", "checkpoint", "interrupted", "notice", "error", "done", "ask_user", "reasoning"]
```

同步修改点：

- `packages/contracts/enums.source.json` → `pnpm gen:enums`
- `packages/contracts/events.schema.json` → `pnpm gen:zod` / `pnpm gen:python` / `pnpm gen:openapi`
- `tests/unit/test_m03_contract.py` 中 kind 计数断言 18 → 19
- `docs/modules/01-共享契约基线.md` §6、`M15` §4.2 事件表、`00-总览与模块地图.md` §9.1 Q6
- 门禁：`pnpm contracts:check`、TS `pnpm --filter @widgetpilot/sdk build`、Python pyright/ruff/pytest

> 设计批准不等于代码已改：以上同步点是 S0 执行清单；当前代码仍为 18 种 kind，验收口径不变。

### 4.2 `reasoning` payload

新增 `$defs.reasoningPayload`，并在 `payload.oneOf` 增加对应分支、`allOf` 增加 fail-closed discriminator（与 `error` / `notice` 同构）：

```json
"reasoningPayload": {
  "type": "object",
  "description": "reasoning：受控推理摘要增量，不是原始思维链",
  "properties": {
    "kind": { "const": "reasoning" },
    "segment_id": { "type": "string", "minLength": 1 },
    "stage": { "enum": ["INTENT", "CONTEXT", "SPEC", "CODE", "GUARD", "SANDBOX", "REPAIR"] },
    "source": { "enum": ["PROVIDER_SUMMARY", "HARNESS_DETERMINISTIC"] },
    "text": { "type": "string", "maxLength": 280 },
    "final": { "type": "boolean", "default": false },
    "redacted": { "type": "boolean", "default": false },
    "truncated": { "type": "boolean", "default": false }
  },
  "required": ["kind", "segment_id", "stage", "source", "text", "final"],
  "additionalProperties": false
}
```

字段语义：

| 字段 | 含义 |
|---|---|
| `segment_id` | 一次推理段的 id；同一段的多条 delta 共用一个 id，前端按段聚合 |
| `stage` | 逻辑阶段，与顶层 `phase`（SessionState）正交，避免把会话状态误当推理阶段 |
| `source` | `PROVIDER_SUMMARY` = L1；`HARNESS_DETERMINISTIC` = L2。前端据此决定折叠与回放策略 |
| `text` | 增量文本；单条 ≤ 280 字符，单段累计 ≤ 2000 字符 |
| `final` | 该段结束；前端在 `final=true` 时 flush 并可解除「生成中」态 |
| `redacted` | 命中敏感过滤，原文已替换为 `<redacted:REASONING>` |
| `truncated` | 触达单条/单段/单 run 段数上限，后续不再产出 |

新增常量进 `enums.source.json.$constants`：`MAX_REASONING_DELTA_CHARS=280`、`MAX_REASONING_SEGMENT_CHARS=2000`、`MAX_REASONING_SEGMENTS_PER_RUN=20`（影子期校准）。

### 4.3 兼容性与状态

- 顶层 `seq` / `sessionId` / `runId` / `traceId` / `phase` 不变；`reasoning` 不改变会话状态。
- 旧前端遇到未知 kind 按 M15 既有规则忽略，不报错、不影响 `done` 判定。
- 无 `done` / `error` 仍视为截断；`reasoning` 不作为终态信号。
- 不新增 `ReasonCode`；超长用 `truncated=true`，敏感命中用 `redacted=true`，不新增错误码。

---

## §5 模型层设计（M03 / M20）

### 5.1 `Delta` 扩展（向后兼容）

```python
@dataclass
class Delta:
    text: str = ""
    channel: Literal["content", "reasoning"] = "content"
    reasoning_final: bool = False
    done: bool = False
```

默认值保持旧调用语义；现有单测无需改即可通过。

### 5.2 ProviderAdapter 接线

- `ProviderAdapter.stream(req) -> Iterator[Delta]` 按 M20 §4.1 已规划协议落地；生产由 `deps.py` 注入真实 adapter，测试用 mock。
- `loop.run` 消费两通道：
  - `channel=content` → 现有 `apply_model_output` 缓冲，行为不变；
  - `channel=reasoning` → 经 `ReasoningPolicy` 归一后 `bus.emit()` 成 `reasoning` 事件；**绝不回注 prompt**。
- chat-completions wire：只接受 provider 明确声明的 summary 字段；若只返回原始 `reasoning_content`，丢弃，不产 L1。
- Responses wire：解析类型化摘要 delta（如 `response.reasoning_summary_text.delta`，最终事件名以 adapter 能力矩阵登记为准）；原始 `reasoning` item 不产 L1。
- 能力矩阵新增 `reasoning_summary: bool`；为 false 时 adapter 不得产 reasoning delta。无真实端点时 fail-fast，不用假摘要冒充。

### 5.3 剥离与归一（`ReasoningPolicy`）

| 输入 | 动作 |
|---|---|
| provider summary delta | 过 §8 过滤 → `Delta(channel="reasoning")` |
| provider raw reasoning / CoT | 丢弃；并从 `ModelOutput.raw` 中递归删除对应字段 |
| 过滤命中 | 原文丢弃，只发 `<redacted:REASONING>`，`redacted=true` |
| 超长 / 超段数 | 截断并置 `truncated=true`，停止该 run 后续 L1 |
| provider 无摘要能力 | 零 L1 事件；L2 由 harness 正常产出 |

---

## §6 传输层设计（M04）

**不用 Pub/Sub 作为正确性依赖**：直接用 Redis Streams 的阻塞读，天然多实例安全，且复用现有 Stream 作为唯一事件源。

1. 连接建立：
   - 无 `Last-Event-ID`：先 `XREVRANGE` 回放最近 N=500 条，记录最后一条 stream id；
   - 有 `seq` 游标：在窗口中找 `seq <= cursor` 的最后一条 stream id，回放窗口内 `seq > cursor` 的事件；若游标已超窗，返回快照提示并从最新 id tail。
2. 实时 tail：循环 `XREAD BLOCK 5000 COUNT 100 STREAMS checkpoint:events:{session_id} {last_stream_id}`；每读一批更新 `last_stream_id`，按帧推荐 `id: {seq}`、`event: {kind}`、`data: {AgentEvent}`。
3. 回放与 tail 的边界用 stream id 衔接，不用 seq 做 Redis 游标，避免重复/漏帧。
4. 心跳 `: keepalive` 每 10s；空闲超过 300s 关闭。响应头保持 `Cache-Control: no-cache` / `X-Accel-Buffering: no`。
5. 背压：单连接缓冲上限 256 帧或落后 30s 即关闭连接；客户端用最后 seq 重连，超窗则拉 `GET /api/sessions/{id}` 快照。不新增错误码。
6. `done` / `error` 仍显式发送；流中没有终态就按截断处理。

该设计闭合 M15 §9.5 缺口 1，且不破坏既有 `Last-Event-ID` 语义。

---

## §7 前端渲染设计（M15 / M17）

- M15 既有 10 类通道扩为 11 类，新增 `ReasoningPanel`（第 11 类），派发规则：
  - `source=HARNESS_DETERMINISTIC` → 状态账内的过程解释，默认可见；
  - `source=PROVIDER_SUMMARY` → 折叠面板，默认收起，标题「查看推理摘要」；展开后按 `segment_id` 分段渲染。
- reducer（M17 `SessionView`）新增：

```ts
type ReasoningView = {
  segments: Map<string, {
    text: string;
    source: "PROVIDER_SUMMARY" | "HARNESS_DETERMINISTIC";
    final: boolean;
    redacted: boolean;
    truncated: boolean;
  }>;
};
```

- 复用既有 rAF 批量 flush（≤60 次/秒）、seq 去重、乱序丢弃、半帧缓存；`final=true` 才解除段级「生成中」态。
- 触达 `truncated` 展示「已截断」徽标；`redacted` 展示 `<redacted:REASONING>`，不尝试还原。
- 跨天恢复：L2 由快照重建；L1 只在 Redis 窗口内可回放，超窗不显示且不给「丢失」错误。
- a11y：`aria-expanded` / `aria-live="polite"`，不抢焦点、不自动滚动覆盖用户输入；遵循 `prefers-reduced-motion`。
- 旧前端遇到 `reasoning` 按未知 kind 忽略，不需要同步发版。

---

## §7A 历史思考过程的进页展示与上滑加载（v0.3，2026-11-23）

> 本节回应用户需求：「用户在前端页面切会话，进页面展示最新的思考过程，往上滑动的时候正常展示以前的思考过程，参考 WorkBuddy / Claude / Cursor 等 agent 应用的实现」。参考这些产品的成熟模式——**最新内容实时置底、历史按游标向前分页加载、思考过程按段折叠**——落到本项目现有架构。

### 7A.1 现状缺口（已核对源码，2026-11-23）

| 能力 | 现状 | 证据 |
|---|---|---|
| 前端会话/消息 UI | **无**，`apps/web/src/` 只有 `main.tsx` + `App.tsx` 健康页骨架，无会话列表/切换/消息列表/EventSource/reducer | `apps/web/src/App.tsx:3-22`；全目录 grep `EventSource|SessionView|reasoning|IntersectionObserver` 零命中 |
| 事件历史持久化 | **无**，事件只写 Redis Stream，按 `maxlen=500` 裁剪、无 TTL；不落 PG | `services/orchestrator/adapters.py:462-479`；migrations 无 `agent_event` 表 |
| 历史事件分页端点 | **无**，SSE 端点仅 `runId` + `Last-Event-ID`，无 `since`/`limit`/游标 | `services/control_plane/api/server.py:617-651`；`services/control_plane/sessions/sse.py:60-85` |
| SSE 实时性 | 发完当前窗口 + 一条心跳即关闭，`XREAD BLOCK` 实时 tail 未做 | `server.py:624-628` 注释；ADR §6 设计为待实现 |
| reasoning 产出 | 契约有简化 kind（`summary`+`source`），生产零 emit | `packages/contracts/events.schema.json:30`；`services/` grep reasoning 零命中 |
| 已有前端设计 | M15 有滚动锚定（仅底部自动滚）、M17 有 event-sourced reducer / scrubber / 从这里重生成 | `docs/modules/M15-分通道流式渲染.md` R6；`docs/modules/M17-时间线回放与状态管理.md` §0.4/§3.2 |

结论：地基（事件协议 + seq 续传 + 会话/任务快照）已就绪，**展示层与历史能力需从零建**，且必须先补历史事件持久化，否则「上滑看更早」无数据源。

### 7A.2 参考 agent 应用的交互模式（借鉴点）

| 产品 | 模式 | 本项目借鉴 |
|---|---|---|
| Claude.ai / WorkBuddy | 思考过程默认折叠成一行「思考了 N 秒」，点击展开分段摘要；最新轮自动展开、历史轮收起 | L1 `PROVIDER_SUMMARY` 默认折叠；L2 `HARNESS_DETERMINISTIC` 默认可见 |
| Cursor / v0 | 消息流按时间倒序可视化，滚到底部为最新；向上滚动逐批加载更早消息 | 列表按 `seq` 升序渲染、底部为最新；上滑触发向前分页 |
| 通用 chat | 游标分页（cursor）+ 保持滚动锚点（加载后视口不跳） | `before_seq` 游标 + IntersectionObserver 哨兵 + 加载前后 scrollHeight 差值补偿 |

不借鉴：原始 CoT 逐 token 打字机（违反 §2.2 非目标）、AI SDK 黑盒前端（违反 D-5）。

### 7A.3 数据层：历史事件持久化（新增，S0 前置）

Redis Stream 500 条窗口不足以支撑上滑看更早历史。新增 PG 表 `agent_event_history`（迁移 0019，建议）：

| 列 | 类型 | 含义 |
|---|---|---|
| `seq` | BIGINT | 全局事件序号（沿用现有 `seq`，每会话内唯一即可） |
| `session_id` | TEXT NOT NULL | 会话（与现有 Stream key 同源） |
| `run_id` / `task_id` / `turn_index` | TEXT / TEXT / INT nullable | 归因（复用现有事件顶层字段） |
| `kind` | TEXT NOT NULL | 事件类型（含 `reasoning`） |
| `phase` | TEXT nullable | 会话状态快照 |
| `payload` | JSONB NOT NULL | 完整 payload（reasoning 段、plan、guard 结论等） |
| `trace_id` | TEXT nullable | 链路追踪 |
| `created_at` | TIMESTAMPTZ NOT NULL | 入库时间 |

- 主键 `(session_id, seq)`；索引 `(session_id, seq DESC)` 支撑向前分页；可选 `(session_id, kind, seq DESC)` 支撑只看 reasoning。
- **写入时机**：事件 emit 时双写——Redis Stream（实时 SSE 用）+ PG（历史用），同一事务边界由编排层保证；PG 写失败按 fail-closed 处理（与 ADR-008 §8 审计纪律一致），不静默丢历史。
- **保留期**：历史事件随会话保留（与 `task_turn`/审计同生命周期），用户删会话时级联清理；不做无限期全局留存。
- **L0 红线**：只落 L1/L2 受控摘要，原始 CoT 在 adapter 边界即剥离（§5.3/§8），PG payload 同样不得含原始推理字段。

> 若不想新增 PG 表：退路是把 Redis Stream 窗口从 500 调大 + 定期归档到对象存储，但游标分页与随机访问的工程复杂度更高，不推荐。本 ADR 以 PG 表为准。

### 7A.4 传输层：历史分页端点（新增）

新增 REST 端点（进 M02 契约门禁，四生成物同步）：

```
GET /api/sessions/{session_id}/events/history
  ?before_seq=<int>&limit=<int=50>&kind=<AgentEventKind?>
  → 200 { code:"OK", data:{ items: AgentEvent[], next_before_seq: int|null, has_more: bool } }
```

- `before_seq` 省略 = 取最新一屏（`ORDER BY seq DESC LIMIT limit` 后反转成升序）；有值 = 取 `seq < before_seq` 的上一屏。
- `kind` 可选过滤（前端可只拉 reasoning 段，减少载荷）；不过滤则返回全部类型（思考过程常与 plan/guard 交错）。
- `next_before_seq` = 本屏最小 seq；`has_more=false` 时前端停止加载。
- 鉴权沿用 `sessions.snapshot(session_id, user.id)` 越权校验（与 SSE 端点同）。
- 上限 `limit ≤ 200`，超界 400。
- **与 SSE 的衔接**：进页面先 `GET history`（最新一屏，如 50 条）渲染 + 记 `last_seq`，再以 `Last-Event-ID=last_seq` 连 SSE 续 tail（§6）；上滑只走 `history` 分页，不干扰实时连接。

### 7A.5 前端渲染流程（M15 ReasoningPanel + M17 reducer）

1. **切会话进页面**：
   - 拉 `GET /sessions/{id}` 快照 + `GET /sessions/{id}/events/history`（最新一屏）；
   - 事件喂给 M17 `reduceSessionView` 归约出 `SessionView`（§7 已有 `ReasoningView.segments`）；
   - 以最新 `seq` 连 SSE 续 tail，新事件继续归约；
   - 渲染：思考过程按 `segment_id` 聚段，`source=PROVIDER_SUMMARY` 默认折叠（最新一轮可短暂自动展开后收起），`HARNESS_DETERMINISTIC` 默认可见。
2. **上滑加载历史**：
   - 列表容器顶部放 `IntersectionObserver` 哨兵；进入视口且有 `has_more` → 触发 `GET history?before_seq=<当前最小seq>`；
   - 返回的旧事件 **prepend** 到列表， reducer 按 `seq` 去重（M17 已有 seq 去重/乱序丢弃）；
   - **滚动锚定**：加载前记 `scrollHeight`，加载后 `scrollTop += (newScrollHeight - oldScrollHeight)`，视口不跳（M15 R6 的对称实现）；
   - 并发去重：同一 `before_seq` 只发一次请求，加载中显示骨架/loading 行。
3. **状态归属**：历史加载的行属于服务端状态（TanStack Query 缓存 + M17 reducer），滚动位置/展开态/哨兵态属于本地 UI 态（Zustand）——沿用 M17 §2.1 分层。
4. **a11y**：折叠按钮 `aria-expanded`；新段到达用 `aria-live="polite"` 不强抢焦点（§7 已有规则沿用）。

### 7A.6 阶段与验收用例

| 阶段 | 内容 | 出口 |
|---|---|---|
| S0' | PG `agent_event_history` 迁移 + emit 双写 + fail-closed | 迁移可逆；emit 后 PG/Redis 都有；PG 失败上抛 |
| S3' | `GET history` 端点 + 前端进页加载 + 上滑分页 + 滚动锚定 | 下列 C11–C14 通过 |

| # | Given / When / Then |
|---|---|
| C11 | 会话有 120 条事件；进页面 → 只渲染最新 50 条，顶部哨兵可见、`has_more=true` |
| C12 | 上滑触发哨兵 → 拉到 seq 50 之前的下一屏并 prepend，视口位置不跳（`scrollTop` 补偿生效） |
| C13 | 连着 SSE 时上滑加载历史 → 实时新事件仍追加到底部，不与历史 prepend 冲突（seq 全局有序、去重生效） |
| C14 | `before_seq` 翻到最早一屏 → `has_more=false`，哨兵停止触发，无重复请求 |

---

## §8 安全、威胁面与合规

- **L0 不可泄漏**：adapter 边界丢弃原始推理；`ModelOutput.raw` 在落库/成本/trace 前递归剥离推理字段；日志与遥测禁止打印原始 delta。
- **不可信输入**：推理摘要不是用户指令，也不是 agent 消息；不进 prompt、不改系统提示、不改能力白名单、不参与路由与终态裁决。
- **过滤**：L1 必须经过现有 PII 真值校验（phonenumbers / id-validator / email-validator）与敏感词机制；命中只写占位符，沿用 ADR-005「只写类别不写原文」的纪律。
- **不是证据**：摘要不进 `error.evidenceRef`，不改变门禁结论，不写入公共语料；用户删除控件时随会话清理。
- **供应商差异**：能力矩阵显式登记是否支持摘要；不得因为某供应商返回原始 reasoning 就自动视为可展示。
- **审计**：只记录「是否开启摘要、source、segment 数、是否 redacted/truncated」，不记录推理正文。

---

## §9 状态、原因码与契约门禁

| 项 | 结论 |
|---|---|
| `SessionState` | 不变，8 值 |
| `ReasonCode` | 不变，17 值；超长/脱敏用 payload 布尔位表达 |
| `AgentEventKind` | 18 → 19（设计已批准；S0 落地时同步 Q6 与计数断言，未改码前仍为 18） |
| `ModelTier` / `GridSpec` / `WidgetType` | 不变 |
| 契约门禁 | `pnpm gen:*` 四生成物 + `pnpm contracts:check` + SDK build + pyright/ruff/pytest；Docker 起依赖后以本机 pytest 为验收口径 |

---

## §10 落地阶段与验收用例（每阶段独立验收）

| 阶段 | 内容 | 出口条件 |
|---|---|---|
| S0 契约先行 | 新增 kind/payload/枚举/生成物/计数断言，不改运行时行为 | `pnpm contracts:check` OK；C01/C02 通过 |
| S1 模型层 | `Delta` 扩展、`ReasoningPolicy`、adapter summary 接线、raw 剥离 | C03–C05 通过；content 输出零回归 |
| S2 传输层 | `XREAD BLOCK` tail、回放/tail 衔接、心跳、背压 | C06–C08 通过；M15 §9.5 缺口 1 关闭 |
| S3 前端 | `ReasoningPanel`、reducer、折叠与 a11y | C09/C10 通过；旧客户端忽略用例通过 |
| S4 影子/内部 | 默认折叠、按用户偏好开启；影子期校准限长常量 | 无 PII 泄漏、首事件延迟与截断率达标 |
| S0' 历史持久化（v0.3） | `agent_event_history` 迁移 + emit 双写 + fail-closed | C11 前置；PG 失败上抛、无 L0 泄漏 |
| S3' 进页/上滑（v0.3） | `GET history` 端点 + 前端进页加载 + 上滑分页 + 滚动锚定 | C11–C14 通过；M15 R6 对称实现 |

可断言用例：

| # | Given / When / Then |
|---|---|
| C01 | `kind=reasoning` 且 payload 缺 `segment_id` → schema 校验失败；`error`/`notice` discriminator 不回归 |
| C02 | 旧客户端收到 `reasoning` → reducer 忽略，`done` 仍可判终态 |
| C03 | mock provider 发 5 条 summary delta + content → 5 条 `reasoning` 事件、1 条正常 content 输出，顺序按 seq |
| C04 | provider 只返回 raw `reasoning_content` → 0 条 L1，`ModelOutput.raw` 中无该字段 |
| C05 | 摘要含手机号 → 事件文本为 `<redacted:REASONING>`，`redacted=true`，日志无原文 |
| C06 | SSE 连接建立后 200ms 发事件 → 客户端 <1s 收到，帧 `id` 等于事件 seq |
| C07 | 在 seq=12 后断开，以 `Last-Event-ID=12` 重连 → 下一条为 13，无重复、无漏帧 |
| C08 | 游标超窗 → 返回快照提示并从最新 id tail，不报错、不阻塞 |
| C09 | 20 段摘要连续到达 → rAF flush ≤60 次/秒，DOM 段数与 `segment_id` 数一致 |
| C10 | axe 扫描 `ReasoningPanel` → 无 serious/critical 项；折叠态 `aria-expanded=false` |
| C11 | 会话有 120 条事件；进页面 → 渲染最新 50 条，顶部 `has_more=true` |
| C12 | 上滑触发哨兵 → 上一屏 prepend 且视口不跳（scrollTop 补偿） |
| C13 | SSE 实时 + 上滑历史并发 → 新事件仍追加底部、seq 去重无冲突 |
| C14 | 翻到最早一屏 → `has_more=false`，哨兵停止、无重复请求 |

---

## §11 拍板结论（2026-11-22，按推荐方案全部通过）

| # | 决策项 | 结论 | 落地影响 |
|---|---|---|---|
| O1 | 展示哪一层 | **已定：L1 + L2，L1 默认折叠** | S0 必须新增 `reasoning` kind；L2 继续由 harness 产出 |
| O2 | 是否允许展示原始 reasoning | **已定：禁止** | adapter 只接受 summary 字段，原始推理字段在 raw 中剥离 |
| O3 | 默认展开还是折叠 | **已定：折叠，用户主动展开** | 避免变成 token 刷屏观感 |
| O4 | 跨天是否恢复 L1 | **已定：不恢复，只恢复 L2** | 无需新增推理正文存储与保留期策略 |
| O5 | 是否新增脱敏 ReasonCode | **已定：不新增，用 `redacted` 布尔位** | 不扩错误码门禁 |
| O6 | 是否批准覆盖声明（§2.3） | **已定：批准，且仅限独立 `reasoning` 通道** | 覆盖声明生效，S0 可开工 |

O1–O6 已全部闭合，无遗留未决项；§10 从 S0 起执行，未改码前契约计数仍按 18 验收。

---

## §12 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 草案 | 2026-11-22 | 首次提出：定义 L0/L1/L2 三层边界、第 19 类 `reasoning` 事件、XREAD live tail、前端第 11 通道、安全剥离规则、S0–S4 验收与 6 项未决；提交用户拍板 |
| v0.2 设计批准 | 2026-11-22 | 用户按推荐方案拍板 O1–O6：L1+L2 且 L1 折叠、禁止原始 CoT、跨天不恢复 L1、不新增 ReasonCode、批准覆盖声明；状态改为「设计已批准 / 实现未开始」，明确 S0 前计数仍按 18，Q6 同步留到 S0 |
| v0.3 历史回看扩展 | 2026-11-23 | 用户要求「切会话进页面展示最新思考、上滑看历史，参考 agent 应用」；新增 §7A：历史事件 PG 持久化（迁移 0019）、`GET /events/history` 游标分页端点、前端进页加载 + 上滑分页 + 滚动锚定，新增 S0'/S3' 阶段与 C11–C14 验收。与 ADR-012（编辑历史对话回退）是相邻但不同的需求：本 ADR 只管「看」，回退「改」归 ADR-012 |
