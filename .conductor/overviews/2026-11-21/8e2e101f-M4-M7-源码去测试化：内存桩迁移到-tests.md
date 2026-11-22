# 会话摘要：M4-M7 源码去测试化（内存桩迁移到 tests）

## L0 索引卡（续接时先读这一段）
- 场景 / 路径：场景 7 项目重构 / 改码（置信度 0.9）
- 当前闸门：全绿（pytest 399 / pyright 0 / ruff 0 / contracts OK / healthz 200 / 台账 strict PASS / review Blocker 0）
- 交付产物：`overviews/2026-11-21/8e2e101f-*.md`、`services/.migration-ledger.json`、`tests/support/**`、迁移后的源码/tests
- 未完成项：S1 SSE 多生产者 seq 冲突、S4 budget=inf（均为跨模块既有待办，非本轮回归）
- 下一步：按 S1 建议统一 RedisEventBus seq 分配（另案），本轮可交付
- 假设：M4-M7 = control_plane(M4/M5) + guardrails(M6) + orchestrator/agent_core(M7)；协议留源码，桩移 tests

## 环境
- 日期时间：2026-11-21 04:44 GMT+8
- 项目：WidgetPilot / 工作区：E:/vsc-workspace/widgetpilot
- OS / Shell：Windows / Git Bash
- 运行时：Python 3.12（.venv）、Node/pnpm、Docker daemon 可用（compose core/full 容器在跑）
- IDE / Agent：WorkBuddy Agent 模式；主控 conductor

## 场景与 skill
- 场景：7 项目重构（源码去测试化 / 行为等价迁移）
- 主控：conductor；工具：handoff（已加载，本会话连续无新 agent，未生成交接文件）、migration_ledger、impact_analysis、code-quality-review
- preflight 降级：脚本按全局 PATH 检查工具，误报 ruff/pytest/mypy/tsc 缺失；实际工具在 `.venv/` 与 pnpm workspace，验收使用仓库惯用命令（见验证结果）

## L1 摘要
### 会话摘要
1. 用户诉求：M4-M7 中内存/Fake 等"测试兼容实现"污染源码；生产只留 PG 实现，测试桩全部移入 `tests/`。
2. S0 已跑：preflight（PATH 误报，已降级）、overview_path（本文件）、env_probe（Docker 可用、真实测试目录 `tests/integration`）。
3. S1 路由已声明：场景 7 改码路径；假设与禁止项见 L0。
4. 验收基线（迁移前）：`pytest -q` 399 passed；pyright 全仓 0 errors；ruff check 全绿；`pnpm contracts:check` OK；TS SDK/BFF/Web tsc 全绿；control-plane healthy。

### 关键决策与理由
- 决策：协议/端口（Protocol）留在源码，内存实现与 Fake 桩移到 tests — 理由：生产装配应只依赖真实 PG/Redis 能力，测试替身归测试所有。
- 决策：不做"保留默认内存实现以兼容旧调用" — 理由：用户明确要求源码干净、按真实环境只需 PG。

### 验证结果
- 迁移前基线：399 passed / pyright 0 / ruff 0 / contracts OK / healthz 200（见 `.workbuddy/memory/2026-11-21.md`）。
- 迁移后终验（同命令实跑）：`pytest -q` **399 passed, 3 warnings**；`npx pyright --pythonpath .venv/Scripts/python.exe` **0 errors**；`ruff check .` **All checks passed**；`pnpm contracts:check` **OK (drift=[])**；control-plane/guardrails `/healthz` **200**。
- 台账：`migration_ledger.py init --path services --merge` → 88 项；`check --ledger services/.migration-ledger.json --path services --strict` → **PASS**（verified 23 / skipped 65 / pending 0）。
- 源码残留扫描：`grep -rn 'class (InMemory|Fake|Stub|...)' services` → 仅保留生产确定性兜底 `DefaultEmbedder`；无 `import tests` 反向依赖；测试侧移动符号无一处仍从 services 导入内存实现。

### S2 迁移计划（已定，按批执行）
1. M04：User/Session 内存 Store、内存 AuditLedger、SSEWindow → `tests/support/control_plane.py`；`create_app`/`SessionService` 改为必需真实依赖。
2. M05：Object/Attachment 内存 Store、FakeTenantGuard/FakeStateSink、InMemoryEventSink、静态 `dispatch` → `tests/support/attachments.py`；`AttachmentService` 强制注入 processor/event_sink，删除 `fail_next_dispatch` 测试钩子。
3. M06：InMemoryAuditStore、InMemoryGuardHitStore+纯函数、三张内存日志表 → `tests/support/guardrails.py`；`validate_chain` 强制注入四表；guardrails `create_app` 强制 hit_store，缺少 DATABASE_URL 时 fail-fast。
4. 保留（非测试兼容件，需显式说明）：所有 Protocol/Row 数据类；PG/S3/Redis 真实实现；`DefaultEmbedder`/`RuleBasedClassifier`/`StructuredFallback` 是 M20/M07 真实模型未接线前的**确定性生产兜底**，不是测试桩，本轮不迁（真实模型到位后另案替换）。
5. 每批迁移后用既有 399 用例作行为等价基线；收尾跑 pyright/ruff/contracts/tsc/容器健康 + 台账 strict + 独立 review。

### 未完成项与下一步
- 已完成：台账建账/收口、逐块迁移、全量回归、独立 review。
- 待跟进（非本轮回归）：S1 SSE 多生产者 seq 冲突（M03/M05/M06 统一序号源）；S4 `budget_left=inf` 静默禁用预算门禁（M11 预算接入时处理）。
- suggested skills：code-quality-review（已执行）

## 独立 review 结果
- 结论：**Blocker = 0 / 无阻塞项**；Should 7 / Nit 3；可交付。详见下方内嵌报告。

## 独立 review 报告
### Round 1：七维 + 接线四问（全新上下文子代理 / code-quality-review）
- 结论：**Blocker 0 / Should 7 / Nit 3；可交付**。经查 399 收集与实跑一致，未见删断言造假绿。
- 关键 Should 与本轮处理：
  - [已修] S2 审计写入失败走 SQLAlchemyError 未进保守拒绝分支 — `guardrail_adapter.py` 的 except 扩为 `(psycopg.Error, SQLAlchemyError)`。
  - [已修] S3 on_fallback 未接线、降级告警丢失 — `build_validator` 传 `on_fallback=lambda reason: logger.warning(...)`。
  - [已修] S5 worker 硬编码开发 DSN/Redis — 改为缺 env 即 `RuntimeError`，移除凭据字面量。
  - [已修] S6 删除了 B2「M05 不置 AWAITING_INPUT」可执行断言 — 新增 `SpySessionService` 并在两处测试恢复断言。
  - [已修] S7 guardrails 模块级 import 即要求 DATABASE_URL、conftest 加占位 DSN — 改 `LazyGuardrailsApp` 惰性构造，测试基座 env hack 删除。
  - [已修] N1/N2 — 删除 validate_chain「默认内存」过时注释；`task_enqueuer` 必填后去掉 None 分支。
  - [待跟进] S1 三个事件生产者各用独立 seq → SSE 续传丢事件（跨模块 I5，另案修 `RedisEventBus.emit` 统一 INCR）。
  - [待跟进] S4 `budget_left=inf` 使预算护栏静默禁用（M11 接入时处理）。
  - [待跟进] N3 need_skill 畸形输入的 INPUT_INVALID 去向口径不一致（当前不传 need_skill_raw，属潜伏路径）。
- Fix loop：Round 1 发现 Blocker 0；本轮主动修复 S2/S3/S5/S6/S7/N1/N2 后重跑全量门禁全绿。Blocker 清零证据：`pytest -q` 399 passed、pyright 0、ruff 0、contracts OK、healthz 200、台账 strict PASS。

## 交接
- handoff 文档：未生成（本会话连续，无新 agent 接手；如中断按本 overview + 台账续接）
- 脱敏声明：本文件不含密钥 / token / PII
