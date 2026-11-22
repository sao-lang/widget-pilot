# Change: 多任务多 Agent + ADR009 完整版 + M08 出护栏沙箱 / 2026-11-22 / confirmed

## Why
用户要求把 ADR-006/007/009/整合设计 与 M08/ADR-004 从设计文档落成真实代码，并在本机 Docker 真实验收。事实核查发现：ADR-009 A 方案只交付事实链（无 generation_task/task_turn/bundle_ref），ADR-006/007/整合设计全是草案未改码，M08 的 output_guard/contract_check/static_scan/sandbox_runner 全缺且 executor.py 自述"无隔离禁止生产使用"。拷问发现四份文档的 AgentEventKind 计数互相矛盾（17/18/19/20）、迁移编号冲突（0012/0013 与已占用 0014）、ADR-007 前置门槛与用户"mock 测试+真实接线"要求冲突。用户拍板：事件 kind 统一到 21；ADR-009 三项前置全量补齐；M08 沙箱走宿主机直调 Docker 的真隔离；分阶段多次提交且不含前序未提交改动。

## Delta
- 事件契约：AgentEventKind 17→21，新增 reasoning / agent_message / review / delegate；events.schema.json 增可选顶层 taskId/taskIndex/role/turn/agentId，plan oneOf 增 TaskPlan 对象分支，guard/validation/sandbox/review 载荷分支，error/notice discriminator 保持 fail-closed；openapi.yaml 补 GET /sessions/{id} 的 tasks[]、GET /tasks/{id}/turns、clarifications.taskId；pnpm gen:all 四同步，contracts:check drift=[]；test_m03_contract 计数改 21。
- DB 迁移（重新编号避开已占用的 0014）：0015_generation_tasks（task_id/parent_run_id/run_id/task_index/task_total/description/grid/attachment_ids/state/result_ref/reason_code/evidence_ref/plan_trace_ref/role/turn）；0016_agent_collab（agent_message hash 链 + agent_workspace rev 乐观锁 + task_turn 版本链，unique(task_id,turn_index)）；0017_session_bundle_ref（sessions.bundle_ref 列，补 B4 缺口）。guard_hit/audit 增 task_id 列（合并进 0015）。
- 任务层：新增 services/orchestrator/tasks/（states.py 内部状态机；ports.py FactSummary/TaskStore/AgentMessage/Workspace/PlanLLM/Generator/Reviewer 端口；planner.py FactExtractor+PlanLLM+PlanValidator 有界修复 1 次+PlanTrace；store.py PG 实现；coordinator.py fan-in 裁决）。dispatcher 增 planning=1 规划任务分流与 N>1 入队；Redis 原子 INCR wp:eventseq:{session_id} 统一 seq 域，next_seq 降级为 seed 兜底。
- ADR-009 完整版：SessionService.complete() 落 bundle_ref 到 sessions；target_task_id 从保留字段转为真消歧（多任务无 target → AWAITING_INPUT+ASK_USER）；task_turn 逐 turn 留 result_bundle_ref/成本；generation_task.current_bundle_ref 更新；旧版本不删可回退。fact_summary 带 task_id/turn_index 维度；结论槽 fact_summary 尾部按 slots.max_tokens 有界裁剪（1–5 槽字节不变）。
- 多 Agent 角色层：中心 Router（6 种有界消息 TASK_ASSIGN/REVIEW_REQUEST/REVIEW_RESULT/REVISION_REQUEST/ESCALATE_HUMAN/FINAL）+ agent_workspace 乐观锁黑板（generator:{task_id}:* / review:{task_id}:*）+ Generator/Reviewer 模型驱动角色循环（MAX_REVIEW_ROUNDS=2，同一 artifact_hash 循环升级）；事件带 role/turn/agentId；Planner/Generator/Reviewer 三个真实模型端口生产 fail-fast、测试 mock；N=1 零回归不启用角色层。
- M08：新增 services/guardrails/{output_guard,contract_check,static_scan,capability}.py（6 类出护栏判据、spec↔manifest↔AST 三方比对+spec_hash、AST 扫 eval/外联/未知 import/系统 API、能力白名单）；新增 services/sandbox_runner/{compile,render,assertions,bridge_stub,evidence,entry}.py（esbuild/tsc 编译、headless/jsdom 渲染、尺寸/运行时/能力/交互断言、bridge 桩、对象存储证据包、一次性容器生命周期）；executor.py 升级为真隔离（--network none / --cap-drop ALL / --user 非 root / cgroups 限额 / seccomp / 15s 硬超时强制销毁）；run() V3 接线出护栏+契约校验+沙箱结论，三类 SSE 事件，沙箱不可用降级为静态校验+只读诊断且 passed=false 不进控件墙。
- 源码净化：删除 run.py:64 生产 _persist 对测试属性 states 的依赖（改真实 CheckpointStore 端口）；清理 run.py:126-183 缺端口降级分支为显式 fail-fast；修 ADR-009 read-turn 失败告警；coding 规则违规一并修正。
- 文档双向同步：ADR-006/007/009/整合设计/M08/M04/M07/M15/M09/00 总览按实现状态更新（不写虚假 Invariant）；ADR-004 §3 设备 API/续航/沙箱校验三项在 M08 模块文档标注为本次落地情况与后续缺口；新增模型缺口文档（真实端点缺失导致哪些链路无法端到端走通）；overview 留痕。
- 提交：按 P0 契约/迁移 → P1 多任务+记忆 → P2 多 Agent → P3 M08 → P4 文档 分阶段提交；每阶段 GREEN 后提交；精确 git add，前序未提交改动不纳入。

## Invariants
- 单任务零回归：N=1 事件无 taskId/role/agentId、入队恰好 1 条、终态与现状逐字节一致；现有 528 passed 基线不降。
- 确定性门禁三明治：V1 安全门禁在规划前且 V1 命中时模型端口调用次数 = 0；V2 计划校验 fan-out 前 fail-closed（幻觉附件 id / 派生描述注入整份拒绝、不落 generation_task）；V3 每个产物出护栏+契约校验+沙箱，Reviewer 不替代 V3。
- 主线不变量：D-5 不引入 Agent 框架（手写消息/黑板/路由）；功能角色不按 widget_type 分化（ADR-004 去类型化不回退）；widget_type 恒 custom 仅契约兼容；非控件意图仍由 M06 拒绝。
- 幂等与隔离：task_id=uuid5(parent_run_id,index) 重规划不重复落行；task_turn.id=uuid5(task_id,turn_index)；每子任务独立 run_id/cost_ledger/checkpoint；子任务失败不拖垮会话（部分成功 COMPLETED+notice，不新增 PARTIAL）。
- 上下文前缀：fact_summary 只在结论槽尾部追加并按 max_tokens 有界裁剪，1–5 槽字节 diff=空；FactSummary 全程零 LLM、PII/注入过滤、8 任务/5 特征上限。
- 沙箱硬边界：生成代码绝不在编排进程执行；一次性容器无网络/非 root/限额/seccomp/超时强杀；证据先于结论（log/snapshot/report 带 hash 才可判 PASSED）；契约不一致不创建沙箱；沙箱不可用绝不判通过、不进控件墙；回边 ≤2 由 M07/M03 计数，M08 不自行重跑生成。
- 消息安全：跨 agent 消息是不可信输入，必过确定性检查与 schema 收窄；payload 只含引用+枚举+结论式短句；Reviewer 无沙箱执行权与终态权；Router 无模型调用权。
- 源码纯净：services/packages 生产源码无 Fake/InMemory/Stub 测试桩、无中文占位串、无 type ignore；测试桩只在 tests/support/；fail-fast 优先于静默降级；新端口复用既有 ports.py 协议。
- 文档与代码一致：设计文档陈述的实现状态必须有代码/测试证据；未实现项在文档 Open/缺口段登记并在代码占位处注释；不改断言造假绿。

## Affected
- packages/contracts/{enums.source.json,events.schema.json,openapi.yaml} + pnpm gen:all 四生成物；packages/agent_core/types.py（AgentEvent/SlotConfig）、packages/agent_core/events/seq.py
- services/control_plane/{models.py,models_fact_summary.py,api/server.py,sessions/service.py,migrations/versions/0015_generation_tasks.py,0016_agent_collab.py,0017_session_bundle_ref.py}
- services/orchestrator/{dispatcher.py,worker.py,validate_chain.py,capabilities.py,memory/*,tasks/*（新增）,cost/*}；packages/agent_core/loop/run.py、context/slots.py
- services/guardrails/{output_guard,contract_check,static_scan,capability}.py（新增）；services/sandbox_runner/{executor,compile,render,assertions,bridge_stub,evidence,entry}.py；infra/sandbox/（一次性沙箱镜像/seccomp）
- tests/unit/ 与 tests/integration/ 新增与增量；tests/support/ 新增端口假实现（仅测试侧）
- 文档：docs/adr/ADR-004/006/007/008/009、docs/多Widget任务的多Agent协作架构-整合设计.md、docs/modules/{00,M04,M07,M08,M09,M15}、docs/modules/M07-模型依赖与测试缺口标注.md（或新增 M08 缺口文档）、.conductor/overviews/2026-11-22/f76a7264-*.md

## Acceptance
- `.venv/Scripts/python.exe -m pytest -q` 退出码 0 且通过数 ≥528（真 PG/Redis 集成真跑，Docker 已起）；新增用例覆盖：规划器的 1/2/5 任务、幻觉附件 id、派生描述注入、有界修复、V1 命中模型零调用；fan-out 3 任务独立 run_id/cost_ledger/seq 无重复；部分失败 COMPLETED+notice；target_task_id 消歧；task_turn 三版产物可回退；跨轮前缀 diff=空；Router 6 消息、黑板乐观锁、返修循环升级；M08 六判据+三方比对+真实 Docker 沙箱+降级不通过。
- `ruff check .`、`npx pyright --pythonpath .venv/Scripts/python.exe`（0 errors）、`pnpm contracts:check` drift=[]、`pnpm gen:all`、`contract_check.py`、`commitment_ledger.py check --strict`、`source_purity.py check --strict`、`review_gate.py`（full 独立子代理）全退出 0；新增代码零 type ignore；source_purity 新增生产文件零命中。
- 真环境验收：alembic upgrade head 到 0017，`generation_task`/`agent_message`/`agent_workspace`/`task_turn`/`sessions.bundle_ref` 在 PG 可查；真实 Docker 一次性沙箱编译/渲染/断言跑通且 --network none 生效；沙箱超时无残留容器；证据对象写 MinIO 且 hash 校验通过；单任务 N=1 主链路真 PG/Redis 回归通过。
- 文档与代码双向一致：ADR-006/007/009/整合设计/M08/00 总览计数与状态同步（21 kind、0017 head、各阶段落地状态）；ADR-004 三项后续缺口在 M08 文档标注；模型缺口文档列明无真实端点导致哪些端到端链路无法走通；代码占位处有明确注释。
- 分阶段提交：每阶段 RED→GREEN+门禁通过后精确暂存提交；前序未提交改动（git status 中既有 M/D/?? 项）不纳入本次提交；git log 可见 5 个左右阶段性 commit。

## Open
- **独立 review（Blocker 3→0）**：Round 1 发现三处主链路断裂——B1 规划入口无人触发（server 入队无 planning 字段）→ 已在 `api/server.py` 入队处按 `needs_planning()` 置 `planning=1`；B2 扇出子任务共用父 run_id 撞 UNIQUE → 已在 `dispatcher._plan_and_fanout` 为每子任务生成独立 uuid4 run_id 并随队下发；B3 fan-in 未接线（子任务各自 complete 撞 only-RUNNING）→ 已加 `dispatcher._fan_in_if_complete`，子任务不各自写终态，全终态后 Coordinator 幂等裁决 + 单次 done + 部分失败 notice。修复有 dispatcher 级真 PG 集成测试证据（`tests/integration/test_m06_fanout_dispatcher_pg.py` 2 passed）。Should S1–S7 已处置：V2 硬拒绝改 fail、task_turn 锁用 advisory、降级恢复 AST 校验等见 overview。
- M20 真实模型端点/key/权重仍缺：Planner/Generator/Reviewer 生产 fail-fast，多任务与角色层端到端真实模型验收登记环境阻断；单任务零回归路径与零 LLM 事实链可照常真实验收；模型缺口文档逐项登记。
- ADR-007 §9 启用门槛（cost per completed task 优于扇出基线）在无真实端点时无法测；本次按用户"mock 测试+真实接线"覆盖前置，A/B 门禁留待 M20 端点就绪后执行，结论写回 ADR-007 Open。
- ADR-004 后续阶段：设备 API 契约/白名单、刷新续航预算（cost_ledger.sandbox_minutes）、设备 API 合规静态校验；本次在 M08 文档标注为部分落地/后续缺口，不在无设备环境冒充完成。
- ADR-008 reasoning 通道 kind 本次随 21 kind 一并加入枚举与 schema，但 SSE 实时 tail（XREAD BLOCK）与前端折叠渲染若不在本轮落地，须在 M15 文档登记为剩余缺口，不宣称已实现。
- 并发上限 WIDGETPILOT_MAX_PARALLEL_TASKS 默认 3/硬 8 为未正式拍板假设，按 ADR-006 O4 推进，M19 评测后回填。
- 前序工作树改动（47 文件，含 PG 真值接线与 overviews 清理）属于其他会话，本次不纳入提交、不回退；若与本轮文件冲突则保留其改动并在其之上叠加。
