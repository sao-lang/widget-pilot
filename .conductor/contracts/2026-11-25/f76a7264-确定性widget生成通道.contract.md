# Change: f76a7264-deterministic-lane / 2026-11-25 / confirmed

## Why
用户连续追问确认：当前意图识别只是 LLM 的挡位分流器，生产链路里没有任何一条真实「不调用 LLM 也能产出 widget」的路径。用户明确要求「比较完整、功能完善的设计，不做简单化，不因为首版就先做个简单版本」，并授权 AI 拍板具体方案。本变更新增一条与 LLM 通道并行的确定性生成通道，覆盖单 widget 与多 widget，全程零 `model.complete` 调用地产出经过同款真门禁、真成包、真终态的 widget。

## Delta
- 新增 `services/orchestrator/deterministic/` 模块：`TemplateRegistry`（全 11 类场景登记）、`try_compile`（槽位→CompileResult|None）、`DeterministicPlanLLM`（实现 `PlanLLM` 协议）、WidgetSpec 构造器、TSX 确定性渲染、manifest_draft 构造、source_ref writer。
- 新增 dispatcher 双分流点：`process_one` 中 N=1 调 `try_compile`、N>1 替换 `PlanLLM` 为确定性实现；均不改 `run()`/`decide()`/`Deps.model`。
- 新增 `_finalize_process(...)`：从 `process_one:821-850` 抽取公共后置义务，正常路径与确定性路径共用（避免旁路复刻导致 ack/fan-in/租约/事件 seq 丢失）。
- 新增 `WIDGETPILOT_DETERMINISTIC` env 开关 + 模板白名单，默认 on，可一键回落 LLM。
- 新增 ADR-013 作为本通道设计权威。
- **v1.1 修正**：替换「带附件一律回落 LLM」为「附件可确定性编译（role 明确 + insight_ref 存在 + 能力兼容）」；修正 `dispatcher.py:569` 写死的 `attachments=()`，新增 AttachmentFact 注入 PlanningFacts 与子任务分配（spec FR-19/FR-24，ADR-013 §4）。原因：附件处理链全零 LLM，一刀切回落属过度保守，且会漏掉「两个 widget + 附件」这类复合场景。
- **v1.2 修正**：替换「grid 未在描述里显式就回落」为「复用 `SPEC_MISSING`→`AWAITING_INPUT`→`clarified_grid` 现成澄清闭环，用户答完用 clarified_grid 编译」（spec FR-2）；新增「长尾/口语描述以 data_contract + grid 双锚定进入编译，不改 L1 词表、不动 route_layer 统计」（spec FR-25，U-17/U-18）。原因：用户指出设计命中面太薄；原门槛是自加保守约束而非系统限制。硬回落边界收窄为三类：实时外部 API / 设备真实调用 / 需模型推理。
- 首个基线（本主题无前序 contract），无删除/替换既有符号；既有 `GatewayPlanLLM`、`run()`、`decide()` 作为回落/默认路径原样保留，不删除。

## Invariants
- 公共契约 schema 不得改：`widget_spec.schema.json`、`widget_bundle.schema.json`、`events.schema.json`、`IntentCategory`/`RouteLayer` 枚举、SDK 四同步产物。
- `run.py:280` 的 `model.complete` 唯一调用点与 `decide.py:95-97` 的 `CALL_MODEL` 默认分支不得改。
- `Deps.model` 必填与 `build_deps` 缺 `MODEL_*` fail-fast 不得改。
- 模板产物必须过与 LLM 产物相同的 M08 三门禁，不得豁免。
- DONE 终态必须带真实 `bundle_ref`（`types.py:386-391`），来自 `assemble()`，禁止占位串。
- N>1 必须经真 fan-in，父会话仅在所有子任务终态后裁决 COMPLETED。
- ADR-004 三条硬样本与 `route_layer` 统计不变（FR-21）。
- 不写 fake/stub 冒充 adapter；真实环境用 podman；回落路径必须真调原端口。

## Affected
- 代码模块：`services/orchestrator/dispatcher.py`（分流 + finalizer 抽取）、`services/orchestrator/worker.py`（装配新端口）、`services/orchestrator/tasks/{ports,planner,fanout,model_ports}.py`（PlanLLM 注入点，不改协议）、`services/guardrails/output_ports.py`（source_ref writer 端口）、`services/control_plane/bundles/service.py`（assemble 调用点）、`services/orchestrator/cost/`（sandbox_minutes 入账）。
- 设计文档（S5 同步核对清单）：`docs/adr/ADR-013-确定性widget生成通道.md`（新增）、`docs/modules/00-总览与模块地图.md`（ADR 索引段）、`docs/modules/M03-手写Harness内核.md`、`docs/modules/M04-账号会话与编排域.md`、`docs/modules/M06-输入校验护栏与路由域.md`、`docs/modules/M08-出护栏契约校验与沙箱测试域.md`、`docs/modules/M09-产物与包管理域.md`、`docs/modules/M09-模型依赖与测试缺口标注.md`（G1/M9-2 重划线）、`docs/M09-代码阅读指南.md`。
- 影响面已核实（`impact_analysis.py --symbols model.complete,run_fanout_planning,validate_and_test,BundleService.assemble,_fan_in_if_complete,_write_back`）：21 处外部引用；S3 需跑 `tests/integration/test_m06_fanout_dispatcher_pg.py`、`tests/unit/test_m06_dispatcher_fanout_wiring.py`、`tests/unit/test_m08_wiring.py`、`tests/conftest.py`。关键结论：`_fan_in_if_complete`/`_write_back` 在 dispatcher.py:841/843 单点调用（抽 finalizer 时两路径都要走）；`run_fanout_planning` 在 dispatcher.py:586 通过 Protocol 注入（确定性 PlanLLM 替换点）；`model.complete` 唯一生产调用点 run.py:280 不动。

## Acceptance
- `python .conductor/contracts/2026-11-25/f76a7264-确定性widget生成通道.coverage.md` 对应门禁：`coverage_check.py --matrix <coverage> --spec <spec>` 退出 0（本轮已实测 34 行 PASS）。
- `contract_check.py --contract <本文件>` 退出 0（六段齐全 + Acceptance 非空）。
- FR-1/2/4/6/21：单测命中/未命中/歧义/长尾 + mock gateway 计数；命中路径调用 0，回落原样，ADR-004 硬样本绿。
- FR-3/5：多 widget 规划单测 N=2/3 + 不可编译回落；可编译时 gateway=0，不可编译回落>0。
- FR-7/8/9：schema 校验 + 两次 compile canonical_json 一致 + `check_contract` 通过。
- FR-10/11：真 podman 沙箱用例 passed + 绕 `_RESULT_CACHE` 用例不短路。
- FR-12/13/14/15：PG 查 bundle 行 + 会话 COMPLETED + 父会话 fan-in + 无 PEL 重投 + 事件 seq 连续。
- FR-16/17：cost_ledger 行 `sandbox_minutes>0`/`tokens=0` + 预算触顶不进沙箱。
- FR-18/19/20/22/23：失败留证 + 附件回落 + 澄清/resume 单测 + 模板版本指纹触发 + 日志/指标可查。
- `npx pyright --pythonpath .venv/Scripts/python.exe` 0 errors；`.venv/Scripts/python.exe -m ruff check .` 全绿；`pnpm contracts:check` drift=[]。
- 写码后必跑 `source_purity.py check --strict` 与 `review_gate.py`（full 档独立子代理）。

## Open
- 无未拍板决策项：U-1～U-14 已按用户 2026-11-25 13:32 授权全部定稿（见 spec §10）。
- 被否决方案（留痕不复活）：A' 简化首版（用户明确否决）、假 `ModelGateway`（违反真实红线）、LLM 首次生成后缓存（无法证零调用）、替换 LLM 通道（破坏开放域覆盖）。
- 范围外独立议题（不在本 contract）：修 `events.schema.json` payload oneOf 漏判；为 LLM 通道串 M08 门禁；设备 API 真实调用与实时外部数据（ADR-010 P-next）。
- 兼容层/双轨：LLM 通道与确定性通道并行属**用户明确要求的双通道**，非临时兼容层；退出条件=无（长期并存，`WIDGETPILOT_DETERMINISTIC=off` 可全量回落 LLM）。
