# M06 · Docker / 真实模型联调测试标记（交付给后续 AI 与用户）

> **状态**：M06 实现已完成并全量单测通过（2026-11-20），但**真实 PG 留证**与**L2/L3 真实模型**属于
> 「需部署环境」的联调项。本文件是这些待办的唯一登记处——后续 AI / 用户按此接手，不要遗漏。
> 关联设计：`docs/modules/M06-输入校验护栏与路由域.md` v1.5（§2.3 运行时依赖、§4.3 可注入端口）。

---

## 0. 一句话现状

| 项 | 当前实现 | 是否阻塞交付 |
|---|---|---|
| 注入/PII/意图/预算 L1 规则 | 纯代码，已实现并测试 | 否 |
| 语义层 / 规格获取 / 冲突矩阵 | 纯代码，已实现并测试 | 否 |
| 技能预取 / need_skill / 命中留证（内存） | 纯代码，已实现并测试 | 否 |
| validate_chain 装配 | 已实现并测试 | 否 |
| 四级路由编排（L1-L4） | **✅ 本模块全做完**：级联判定/升级判据/降级归因/留证/默认兜底；三个 Protocol 接线点见 `ports.py` | 否 |
| L2 语义路由 | **默认纯代码 Embedder（可离线跑）**；`Embedder` Protocol 接线点已留 | 否（默认实现可用） |
| L3 小模型分类 | **默认规则兜底（返回不可解析→升级）**；`IntentClassifier` Protocol 接线点已留 | 否（默认实现可用） |
| L4 主 LLM 兜底 | **默认结构化兜底**；`IntentGateway` Protocol 接线点（v1.5 补齐）已留 | 否（默认实现可用） |
| `guard_hit` 持久化（PG 表 + prev_hash） | **已落 PG**（2026-11-21）：迁移 0007 + `PgGuardHitStore`，hash 链 + PG 失败保守拒绝 | 已完成，见 §2 |
| 真实 embedding 权重 + 意图样本库 | **未提供**，infra/models 不存在 | 联调项，见 §3 |

---

## 1. 如何跑测试（本机唯一验收环境）

> 项目约定：**本机验收唯一标准** = Docker daemon 起 + `docker compose --profile core up -d`
> + `.venv/Scripts/python.exe -m pytest -q`。CI 结果不作验收依据。

```bash
cd E:/vsc-workspace/widgetpilot
# 1) 起基础设施（若未起）
docker compose --profile core up -d
# 2) 全量测试
.venv/Scripts/python.exe -m pytest -q
# M06 验收看 1.1 的 104 passed；全量数因 M04/M05 并发开发波动（340-353 passed），非 M06 回归
# Docker 停掉时：集成用例 skip，属环境问题非代码回归
```

### 1.1 只跑 M06 测试

```bash
.venv/Scripts/python.exe -m pytest -q tests/unit/test_m06_*.py
# 本次实测：104 passed（含 15 mock 接线 + 12 完整性 + 4 API + 2 轮 review 修复断言）
```

### 1.2 M06 模块覆盖率（口径：只算 services.guardrails，排除预置 health stub）

```bash
.venv/Scripts/python.exe -m pytest -q --cov=services.guardrails --cov-report=term \
  tests/unit/test_m06_*.py
# 本次实测：TOTAL 97%（api/main.py 本轮已补端点，计入统计）
# 含 telemetry.py / change_gate.py 两份新增只追加日志与门禁钩子实现
```

> 注意：覆盖率命令必须用 `.venv/Scripts/python.exe -m pytest`，不要用裸 `pytest`——
> 后者在本仓库会丢失 `packages` 包的 sys.path，导致 conftest 导入失败。

---

## 2. 【已完成 2026-11-21 · PG 留证联调】guard_hit 持久化

> **落地记录**：迁移 `0007_guard_hit.py`（0004 已被 M05 占用，顺延）+ `services/guardrails/guard_hit_pg.py`
> （`PgGuardHitStore`：hash 链 + 事务级 advisory lock 串行化追加防断链）+ 装配层注入 + PG 失败保守拒绝；
> 集成测试 `tests/integration/test_m06_guard_hit_pg.py`。以下原 TODO 清单保留作设计依据。

**文件**：`services/guardrails/guard_hit.py`（接口）+ `services/guardrails/guard_hit_pg.py`（生产 `PgGuardHitStore`）
**设计依据**：M06 文档 §6.1 字段表、`guard_hit` 表 + `prev_hash` hash 链、§2.3「PG 不可用 → 保守拒绝」。

待办清单（后续 AI 接手）：
1. 新建迁移 `services/control_plane/migrations/versions/0004_m06_guard_hit.py`，字段按 §6.1：
   `id / session_id / run_id / hit_type / rule_id / judgement / excerpt / stage / decision /
   trace_id / prev_hash / row_hash / should_pass / created_at`，前四留证字段 NOT NULL。
2. 实现 `PgGuardHitStore`（SQLAlchemy ORM + pg8000，仿 `PgCheckpointStore` 写法，见 `services/orchestrator/adapters.py`），
   实现 `log_hit()` 与 `false_positive_rate()` 的 PG 版；写入前校验 hash 链完整性。
3. 装配层在 PG 不可用时**保守拒绝放行**（§2.3），不放行进模型——这条不能降级。
4. 集成测试放 `tests/integration/test_m06_infra.py`，复用 `test_m04_infra.py` 的连通性 skip 守卫，
   **不要**用内存替身冒充 PG 留证验收。

**验收命令**（实现后）：
```bash
docker compose --profile core up -d
.venv/Scripts/python.exe -m pytest -q tests/integration/test_m06_infra.py
```

---

## 3. 【TODO · 真实模型联调】L2 embedding / L3 分类器

> **逐项缺口标注**：哪些功能因模型缺失无法真测、各自所需条件与 mock 覆盖范围，见
> `docs/modules/M06-模型依赖与测试缺口标注.md`（本次功能完整性审查产出）。

**口子位置**（设计 §6.2.1 接线清单，M06 侧已全部落地）：
- `services/guardrails/ports.py` — 三个正式 Protocol 契约（L2 `Embedder` / L3 `IntentClassifier` /
  **L4 `IntentGateway`**，后者 v1.5 补齐），`runtime_checkable` 可供实现方自检。
- `services/guardrails/intent_cascade.py::classify_intent(embedder=, classifier=, gateway=, on_fallback=)` —
  统一注入入口，含降级编排 + `fallback_reason` 留证。
- `services/guardrails/validate_chain.py` 同参数透传到级联。
- 默认零依赖实现：`DefaultEmbedder` / `RuleBasedClassifier` / `StructuredFallback`（未注入即生效并告警）。

**本模块已完成**：级联判定、升级判据、降级归因、默认兜底、Protocol 契约、14 个 mock 用例
（`tests/unit/test_m06_routing_ports.py`）。**仅剩** M20/M07 的真实能力类按 Protocol 注入。

默认实现（已可离线跑，不用管本节）：
- L2：`DefaultEmbedder` = 字符 bigram + 关键词权重，纯 Python 零依赖。
- L3：`RuleBasedClassifier` 返回不可解析 → 级联按结构化判据升级，不猜意图。

真实模型接入步骤（后续 AI / 用户）：
1. 把 embedding 权重与意图样本库放到 `infra/models/intents/<version>/`
   （样本库 ≥30 条/意图，封闭意图集合见 §5.1 / ADR-001 D-8）。
2. 在 `docker-compose.dev.yml` 的 `guardrails` 服务卷挂载 `./infra/models:/app/infra/models:ro`
   （M01 容器挂载点，当前未配——这是待办）。
3. 实现 `RealEmbedder`（经 M20 `EmbeddingAdapter` / sentence-transformers，满足 `ports.Embedder`），
   注入 `classify_intent(embedder=...)`；实现 `RealIntentClassifier`（经 M07 `ModelGateway` 低档端点，
   token 上限 `MAX_CLARIFY_TOKENS=512`，满足 `ports.IntentClassifier`），注入 `classify_intent(classifier=...)`；
   实现 `RealIntentGateway`（经 M07 `ModelGateway` 前沿档位，满足 `ports.IntentGateway`），
   注入 `classify_intent(gateway=...)`。三者的 mock 对照见 `test_m06_routing_ports.py`。
4. 版本化：样本库版本 `INTENT_SAMPLES_VERSION`、模型版本入 `IntentRouteLog` 与 `routing` 事件，
   变更触发 M10 回归门禁（§9.3）。
5. 影子期校准阈值 `SEMANTIC_ROUTE_THRESHOLD`（当前 0.75 建议初值），升级率目标 < 30%。

**验收**（真实模型就位后）：跑 A22/A23/A25（test_m06_acceptance.py）改用 Real 端口，
确认拒绝类路径 L2/L3/L4 调用次数仍恒 0（A24，红线不会因换模型而破）。

---

## 4. 已知工具/门禁环境说明（交给后续 AI 避坑）

- `conductor/scripts/verify.py --lang python` 的失败项（0/5）是**环境/模板不匹配**，非本模块回归：
  - `mypy --strict src` / `pydocstyle src`：本仓库无 `src/` 目录（根即 python path），命令模板不适用；
  - `pytest -q --cov=src`：`src` 不存在，且裸 `pytest --cov` 会丢 `packages` sys.path；
  - `ruff check .` 全库有 6 条**预置**问题（`packages/contracts/enums.py`、
    `packages/sdk/python/widgetpilot_client/models.py`），均在本模块之外。
  - 本模块范围的真实证据：`ruff check` / `ruff format --check` 全绿，覆盖 97%，104 单测通过。
- 缺失工具已通过 `.venv/Scripts/python.exe -m ensurepip --upgrade` +
  `pip install mypy pydocstyle pytest-cov` 补装（2026-11-20）；若新环境未装按此命令重装。

---

## 5. 交付标记速查（grep 这些关键词定位 TODO）

| 标记 | 含义 | 位置 |
|---|---|---|
| `【已完成 · PG 留证联调】` | guard_hit 持久化已接 PG（2026-11-21，迁移 0007） | 本文档 §2 |
| `【TODO · 真实模型联调】` | L2/L3 真实模型待注入 | 本文档 §3 |
| `【v1.5】` | 设计拷问后的修正点 | M06 设计文档 + 代码注释 |
| `可注入端口` / `Embedder` / `IntentClassifier` | 真实模型替换点 | semantic_router.py / intent_classifier.py / intent_cascade.py |
