# 会话摘要：多 widget 请求的主控调度与子任务并发设计

## L0 索引卡（≤8 行）
- 场景 / 路径 / 档位：2·3 设计 / 文档（router → brainstorm → ADR 草案 → 用户拍板）/ lite，置信 0.85
- 声明：目标=给一次对话生成 2+ widget 的可评审方案；下一站=用户确认 O4 后进 S2 规格固化 + change-contract；不改码
- 省掉：impact / subagent / tdd / verify / review_gate / ledger / git / change-contract（无代码变更且规格未确认，依据 lite 条件加载表）
- 事实基线：当前严格 1 session → 1 run_id → 1 队列任务 → 1 agent loop；无 task_id / 任务图 / fan-out；seq 由事件重算，SSE 单 session key
- 产物：`docs/adr/ADR-006-一次对话多控件生成的主控调度与子任务并发.md`（v0.2 草案：约束式 LLM 规划 + 校验三明治）+ `docs/modules/00-总览与模块地图.md` ADR 索引同步
- 已拍板：O1 确定性主控（规划器才调模型）/ O3 COMPLETED+notice / O5 仅歧义确认；O2 用户否决纯规则，改为事实抽取+LLM 约束规划+零模型计划校验（§6.7）；O4 并发上限按默认 3 硬 8 假设，待确认
- 降级：仅做只读探索 + 文档，未跑 pytest/contracts（无代码改动）；数字均引自源码/现有文档，未凭记忆编造

## 追加：ADR-007 多 Agent 角色协作（用户追问"真要拆多 agent 怎么做"）

- 场景 / 路径 / 档位：2·3 设计 / 文档（沿用本 overview 的 router→brainstorm）/ lite；置信 0.8
- 声明：目标=给真正的多 agent 角色协作给出可评审架构；下一站=O1–O5 拍板 → 规格固化；不改码
- 省掉：同 L0（无代码变更、规格未确认）；新增 ADR-007 草案 + 索引同步
- 核心：功能角色（Planner/Generator/Reviewer）+ 中心路由类型化消息 + 版本化黑板 + 三级预算/终止协议；批准即正式撤销 §1.7 非目标
- 硬门槛：ADR-006 P0–P2 + M20 真模型端点 + 沙箱并发压测；启用门槛为 cost per completed task 优于扇出基线
- 未决：O1 角色维度（功能角色推荐）/ O2 是否坚持 D-5 手写 / O3 中心路由 vs 点对点 / O4 启动时机 / O5 撤销 §1.7

## 追加：用户澄清"整合"含义 + 文档整合完成

- 用户拍板：ADR-007 O1 功能角色 / O2 手写最小协议（D-5 保留）/ O3 中心路由+类型化消息；O4 明确为"先写文档，把 ADR-006 和 ADR-007 整合"。
- 用户进一步澄清："整合"= **多 widget 任务下的多 agent 架构直接接进来**，不是两个方案二选一。已据此写 `docs/多Widget任务的多Agent协作架构-整合设计.md`：N>1 默认走任务扇出 + 每任务内 Generator/Reviewer 协作，N=1 零回归。
- 已同步 ADR-006 §2.1（合并后多 widget 整体是多 agent）、ADR-007 头部/§2 接入方式/O4、模块地图统一阅读入口。
- 仍未改码、未跑测试；代码阶段待 ADR-006 P0–P2 + M20 端点 + 沙箱压测。
