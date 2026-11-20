# 会话摘要：全仓动态类型消除（Any/object/dict[str,object] → 确定类型）

## L0 索引卡（续接时先读这一段）
- 场景 / 路径：场景 7 项目重构 / 改码（置信度 0.85）
- 当前闸门：全绿（pytest 399 / pyright 0 / ruff 0 / contracts OK / TSC 绿 / healthz 200 / 台账 strict PASS / review Blocker 0）
- 交付产物：本 overview、`packages/contracts/types.py`、改造后的源码/测试/SDK 生成器
- 未完成项：无（S1–S4 四条 review Should 已全修；N3 豁免注释已补）
- 下一步：可交付；后续如接 M11 再处理 ToolSpec.handler 的动态返回
- 假设：范围=services/ + packages/ + tests/ 的 Python 源码；生成物按例外处理

## 环境
- 日期时间：2026-11-21 05:44 GMT+8
- 项目：WidgetPilot / 工作区：E:/vsc-workspace/widgetpilot
- OS / Shell：Windows / Git Bash
- 运行时：Python 3.12（.venv）、Node/pnpm、Docker daemon 可用
- IDE / Agent：WorkBuddy Agent 模式；主控 conductor

## 场景与 skill
- 主控：conductor；工具：handoff（已加载，本会话连续无新 agent，未生成交接文件）、migration_ledger、impact_analysis、code-quality-review
- preflight：沿用上一会话结论（按全局 PATH 误报工具缺失；实际 `.venv/` + pnpm workspace 齐全，验收用仓库惯用命令）
- env_probe：Docker 可用、真实测试目录 `tests/integration`、需真实环境验证

## L1 摘要
### 会话摘要
1. 用户诉求：代码中有很多动态类型（`Any`、`object`、`dict[str, object]`），需要确定类型；按 conductor 执行。
2. 初盘（grep，含 tests）：Any 84 处（services 25 / packages 41 / tests 18）、dict[str, object] 52 处（22/1/29）、object 注解 43 处（21/3/19）；188 个 py 文件。
3. 上一会话基线：pytest 399 passed；pyright 0 errors；ruff 全绿；contracts OK；healthz 200。
4. 已启动穷尽清单子代理（只读），正在逐个核对真实取值形态与建议确定类型。

### 关键决策与理由
- 待定：JSON/跨进程边界是否允许具名 alias（编码规则 §1 允许 JSON 边界用 `type JsonValue = ...`）；反射边界是否豁免。将按穷尽清单逐条定性。

### S2 迁移计划（按批，行为等价）
1. **共享确定类型**：JSON 边界统一用 `packages/agent_core/types.py` 已有 `JsonValue`/`JsonObject` 具名别名；新增确定结构的 TypedDict（API 信封 `ApiEnvelope`、校验入参 `ValidationFields`、任务 `TaskFields`、事件 payload 各 kind）。
2. **P0 契约/API**：`services/control_plane/api/server.py` 的 `_ok`/`_m05_ok`/`_m05_error`/各路由返回、`services/guardrails/api/main.py` 的 `_ok` → 确定信封类型；SDK 生成器 `gen_python.py` 把 `object`→`JsonObject`、`array`→`list[JsonValue]`、`ApiResponse.data`→`JsonValue | None`（改生成器 + `pnpm gen:python`，不改产物源码）。
3. **P1 核心领域**：`validate_chain` 的 `spec_missing`/`spec_conflict`（用现成 `SpecMissing`/`SpecConflict` 模型，顺带清 4 个 `type: ignore[attr-defined]`）；`ValidatorFn`/`dispatcher.fields`/`guardrail_adapter.validator` 用 `ValidationFields`；`deps.rows` 复用 agent_core `CheckpointRow` TypedDict；Attachment 事件 payload 用确定 TypedDict。
4. **P1 反射边界**：psycopg `cast(Any, dict_row)` 收敛为具名别名 + 注释说明（规则 §1 允许的反射边界，禁裸 Any 散落）。
5. **P2 tests/support**：测试替身签名随生产类型收紧；`*args: Any` 测试假函数改为精确签名或具名 alias。
6. 每批后跑 pyright/ruff；全批完成后跑 pytest 399 + contracts + tsc + healthz + 台账 strict + 独立 review。

### 豁免候选（待穷尽清单确认）
- JSON/外部 payload 边界 → `JsonValue`/`JsonObject` 具名别名（编码规则 §1 允许）。
- psycopg row_factory 反射、FastAPI 框架注入边界 → 具名 alias + 行内注释说明。
- SDK 生成物 `packages/sdk/python/widgetpilot_client/` → 改生成器而非产物。

### 穷尽清单结论（生产代码，84 条）
- 裸 Any 21 / object 注解 33 / dict[str,object|Any] 17 / type:ignore 13（有交叉）。
- 可消掉 74/84（88%）；豁免 9（psycopg row_factory×2、FastAPI 联合 response_model×3、ASGI 边界×4）；SQLAlchemy cursor 1。
- 服务层 0 处引用已有的 `JsonValue`/`JsonObject` 别名——这是散落动态类型的根因。

### S3 执行进度
- [已完成] P0 共享类型基座：新增 `packages/contracts/types.py`（ApiEnvelope/TaskFields，re-export agent_core 的 JsonValue/JsonObject）。
- [已完成] P0 SDK 生成器：`gen_python.py` 把 object→JsonObject、array→list[JsonValue]、data→JsonValue，生成物自包含别名（SDK 独立分发不依赖 agent_core）；生成物 Any 28→0；contracts:check OK。
- [进行中] 三个并行 agent：P1 核心（orchestrator/guardrails/agent_core）、P2 控制面（control_plane/api）、P2 测试侧（tests）。

### 终验数据（改造前 → 后）
| 形态 | 改前 | 改后 | 剩余性质 |
|---|---|---|---|
| 裸 Any 注解（生产） | 21 | 5 | FastAPI 联合 response_model×3 + psycopg row_factory×2，均登记豁免 |
| object 注解（生产） | 33 | 1 | SQLAlchemy CursorResult 反射豁免 |
| dict[str, object] | 17 | 3 | ASGI `_Scope/_Receive/_Send` 边界豁免 |
| type: ignore | 13 | 1 | ASGI 转发边界豁免 |
| SDK 生成物 Any | 28 | 0 | 生成器 object→JsonObject / array→list[JsonValue] / enum→str |
| 测试侧动态类型 | ~60 | ~4 | 反射/JSON 边界 cast |

### 关键决策
- `JsonValue`/`JsonObject` 权威仍在 `packages/agent_core/types.py`；`packages/contracts/types.py` re-export 并新增 `ApiEnvelope`/`TaskFields`——避免大规模改动 agent_core 30+ 引用。
- SDK 生成物自包含别名（不依赖 agent_core），保持独立分发；生成器同步改造（改生成器不改产物源码）。
- `AttachmentServicePort | None`：None 表示"不挂载 M05 路由"的部署形态，生产装配恒注入真实服务，不违背上一轮"依赖必填、禁兼容桩"决定。
- `ApiEnvelope = dict[str, JsonValue]` 实测作为 FastAPI response_model 不过滤字段，与原 `dict[str, Any]` 行为一致。

### 验证结果
- `pytest -q` **399 passed, 3 warnings**；pyright 全仓 **0 errors**；`ruff check .` **All checks passed**；
- `pnpm contracts:check` **OK (drift=[])**；SDK/BFF/Web tsc 全绿；8000/8100 healthz **200**；
- 台账 strict **PASS**（87 项收口：verified 19 / skipped 68）；
- 反向测试：篡改生成器镜像字段后 `contracts:check` 正确 FAILED，证明新增校验有效。

## 独立 review 结果
- 结论：**Blocker = 0 / 无阻塞项**；Should 4 / Nit 3。可交付。

## 独立 review 报告
### Round 1：七维 + 接线四问（全新上下文子代理 / code-quality-review）
- [已修] S1 `intent_cascade.py:128` on_fallback 漏消裸 object → 改 `Callable[[str], None]`。
- [已修] S2 生成器 enum/const 字段误回退 JsonValue（9 字段）→ `py_field` 分支直接定 `str`，重生成 SDK 后 kind/phase/grid 等为 str。
- [已修] S3 两处 B2 断言被改成永真式（`test_m05_service.py`/`test_m05_completeness.py`）→ 删除无关 SpySessionService 断言，保留 notice 事件断言。
- [已修] S4 `_API_RESPONSE_FIELDS` 手写镜像无校验 → `check.ts` 新增第 5 步比对 openapi.yaml 字段集合，反向测试通过。
- [已修] N3 `ToolSpec.handler` 补框架边界豁免注释。
- [保留] 其余裸 Any/object/dict[str,object] 经逐条核对均为真实框架/反射边界，注释登记齐全。
- Blocker 清零证据：pytest 399 passed、pyright 0、ruff 全绿、contracts OK、tsc 绿、healthz 200、台账 strict PASS、review_gate PASS。

## 交接
- handoff 文档：未生成（本会话连续，无新 agent 接手）
- 脱敏声明：本文件不含密钥 / token / PII

## 交接
- handoff 文档：未生成（本会话连续，无新 agent 接手）
- 脱敏声明：本文件不含密钥 / token / PII
