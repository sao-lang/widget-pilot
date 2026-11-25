# Change: f76a7264-deterministic-wiring-p1 / 2026-11-25 / confirmed

## Why
P0 已打通确定性通道（`services/deterministic/`，11 单测 + 1 podman 真沙箱集成测试全绿、零云端 LLM），但生产 dispatcher 的 DONE 分支仍只读模型自报 `bundle_ref`（`dispatcher.py:451-464` 留有「M08 V3 强制门禁待接」TODO），用户 18:08 确认按 6 阶段计划执行，本轮 P1 = 把已跑通的通道接进生产：`validation.ok` 后带开关分流，命中compiled 走三门禁+真成包，fallback 继续原 LLM 链路。缺口性质=生产未接线，不是通道没打通。

## Delta
- `services/orchestrator/dispatcher.py`：`Dispatcher.__init__` 增可选注入 `deterministic_runner`（鸭子协议，返回 dict：branch/bundle/refused reason）；增 `WIDGETPILOT_DETERMINISTIC` 开关（off 时钩子不生效，零行为变化）。`process_one` 在 `validation.ok` 之后、`run()` 之前尝试确定性通道：compiled → 用通道返回的 bundle_ref 构造 `State.terminate(Decision(DONE), bundle_ref=)` 作为 run() 的等价终态，后续 task_turn/update_state/fan_in/write_back/ack 全部照常；llm_fallback → 继续 `run()`；rejected/clarification → 构造对应终态/挂起态走原后置路径。
- `services/orchestrator/worker.py`：装配真实确定性 runner 注入 Dispatcher（复用已注入的 bundle 端口/证据存储/源码存储/容器 CLI），缺配置时不开通道（fail-open 到 LLM，非造假）。
- 新增 `tests/unit/test_v3_deterministic_wiring.py`：命中分流/开关/fallback/四分支回写/后置义务不丢，全用桩端口。
- 无公共 JSON Schema 变更、无 DB 迁移、无 events.schema 变更（通道结果走内部 dict，不进事件 payload）。

## Invariants
- 同一安全校验只做一次：复用 `validate_chain` 的 `validation` 结论，确定性通道不重复跑进护栏/越界/PII 判定；解释器不产拒绝类。
- 命中分支必须履行 `process_one` 全部后置义务：task_turn（如适用）、`task_store.update_state`、`_fan_in_if_complete` 或 `_write_back`、`queue.ack`；禁止旁路丢状态导致 PEL 重投/父会话永不 COMPLETED。
- `WIDGETPILOT_DETERMINISTIC=off` 或未注入 runner 时，`process_one` 行为与现状逐字一致（零注入、零通道调用）。
- DONE 终态必带真实 `bundle_ref`（来自 `BundleService.assemble` 返回），非模型自报空串；三门禁未过不得成包。
- 零云端 LLM：确定性路径 `model.complete` 计数为 0；llm_fallback 交原 run() 是显式例外且标注 `generation_path=cloud-fallback`。
- ADR-004 去类型化不倒退：`ValidationResult.widget_type` 恒 `custom`。

## Affected
- 代码：`services/orchestrator/dispatcher.py`（构造 + process_one 分流）、`services/orchestrator/worker.py`（装配）、`services/deterministic/`（P0 已建，本轮不改其核心）、`tests/unit/test_v3_deterministic_wiring.py`（新增）。
- 契约：无公共 schema/迁移/事件变更；本契约 confirmed 即规格确认，覆盖矩阵 route.py 判定不需要。
- 文档：`docs/adr/ADR-013` §8 诚实缺口更新「生产接线状态」一行；`.workbuddy/memory/2026-11-25.md` 追加 P1 结论。
- 影响面（已核实，主工作区）：Dispatcher 构造点 `worker.py:187`（生产）+ 9 处测试（`test_m06_wiring.py:129/225/299`、`test_m07_multiturn_wiring.py:62`、`test_m04_dispatcher.py:162/222/275`、`test_m06_wiring_pg.py:106/162/267`、`test_m06_fanout_dispatcher_pg.py:107/143`）；后置义务 `dispatcher.py:817-858`（run→write_task_turn→task_store.update_state→fan_in/write_back→ack）；分流点 `dispatcher.py:767 validation` → `:775 run()`。省略外部 worktree `widgetpilot-m10-m11/`（未跟踪副本，不属本仓基线）。

## Acceptance
- contract_check 六段齐全 PASS；无公共契约/DB 变更声明。
- TDD RED 先失败（钩子签名/分流断言），GREEN 全过；接线单测覆盖 compiled/fallback/rejected/clarification/开关 off 五类。
- 命中 compiled 时：`model.complete` 计数 0、`bundle_ref` 非空且来自 assemble、`task_store.update_state`/`fan_in`/`queue.ack` 全被调用（后置义务不丢）。
- llm_fallback 时继续调 `run()`，且通道结果标注 `generation_path=cloud-fallback`。
- 全量 pytest 零回归（基线 792 passed 11 skipped）；ruff 绿 / pyright 0 errors / contracts drift=[] / source_purity 新增目录零污染。
- 独立 review Blocker=0。

## Open
- 范围外（按 6 阶段计划排后续）：P2 会话 widget 注册表+查询工具+修改定位；P3 本地 BIO 权重+命中率；P4 附件全链路；P5 返工+压缩降级；P6 多配方+N>1。
- 诚实缺口仍在：本地 BIO 权重/视觉端点/LLM 端点无；多配方只有 task.list；N>1 确定性 PlanLLM 未接。
- 否决留痕：不改公共 schema/events；不在 DONE 分支里二次调用三门禁（那会丢 run() 终态语义）；不建新旧双轨开关（只有 `WIDGETPILOT_DETERMINISTIC` 单开关）。
- 环境：podman 6.2 在 `D:/apps/podman`，真沙箱单独用 `WIDGETPILOT_CONTAINER_CLI_PATH` 跑（不污染全量 docker 桩测试）。
