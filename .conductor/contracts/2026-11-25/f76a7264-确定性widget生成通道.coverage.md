# 需求覆盖矩阵 · 确定性 widget 生成通道（v1.0 完整版）

> 需求列来自用户本轮原话（逐条抄），不改写为规格术语。决策列四类：采用 / 否决 / Open / 范围外。
> 规格：`f76a7264-确定性widget生成通道.spec.md`（v1.0，用户 2026-11-25 授权 AI 拍板，U-1~U-14 全定）。
> **本矩阵已无 Open 行**：原 R9–R13/R23–R30 的 Open 项已按用户「具体方案你定」全部拍板为采用。

| 需求 | 决策 | FR | 验收 |
|---|---|---|---|
| R1 「这里意图识别的目的是什么？」 | 采用：定位为路由分流器，非生成器 | FR-4 | §2 源码行号 + overview S1 基线 |
| R2 「不对，那这样不叫意图识别，意图识别不应该是确定走什么链路，不用调用llm吗？」 | 采用（诚实化）：意图识别仍为挡位分流；链路裁定由 dispatcher compile 端口 + 确定性 PlanLLM 承担 | FR-4,FR-5,FR-21 | `intent_cascade.py:53-73` + route_layer 统计不变 |
| R3 「现在是这样子吗？」 | 采用 §2 事实基线：L1 恒零模型，L2/L3/L4 可选端口、生产不注入 | FR-4 | `worker.py:172-175` / `guardrail_adapter.py:8-10` |
| R4 「有没有精准预测，不使用llm的链路，现在只是llm的挡位分流器，并没有真实不走llm的链路」——核心诉求 | 采用方案 A 完整双通道 | FR-1~FR-6 | N=1 与 N>1 命中路径 gateway 计数 0 |
| R5 「这个要怎么做？你先出一个方案出来」 | 采用：S2 规格 + 覆盖矩阵 | FR-1（规格定义 compile 接口，即方案本体） | 本矩阵 coverage_check rc=0 |
| R6 「你先出个方案吧，按照 @skill:conductor」 | 采用：按 conductor 场景3/doc/full 走 S0→S2，覆盖矩阵 + 红队复审 | FR-23（规格定义可观测，方案交付含门禁证据） | overview S1/S2 结论 + coverage_check PASS |
| R7 「我需要一个比较完整 功能完善的设计，不做简单化，不因为首版就先做个简单版本，具体方案你定」 | 采用 v1.0 完整版：全场景模板登记 + 确定性 PlanLLM 覆盖 N>1，否决 A' 简化首版 | FR-1,FR-3,FR-5,§7.4 | §7.4 全 11 类场景登记；N>1 零调用用例 |
| R8 「具体方案你定」——授权 AI 拍板所有未决 | 采用：U-1~U-14 全部定稿进 §10，无 Open | FR-1~FR-23 | §10 决策清单逐条有结论 |
| R9 方案必须真产出 widget | 采用 FR-7~FR-12 | FR-7,FR-8,FR-9,FR-10,FR-11,FR-12 | 真 podman 沙箱 + PG 查 bundle |
| R10 不能是假实现/冒充已接线 | 采用 §11 红线 + U-5 诚实措辞 | FR-11,FR-12,FR-18 | source_purity + 真集成用例 |
| R11 成本诚实 | 采用 FR-16/17 | FR-16,FR-17 | sandbox_minutes>0 / tokens=0 |
| R12 多 widget 也要真零调用（完整版推论） | 采用 FR-3/5：确定性 PlanLLM 注入 PlanLLM 协议 | FR-3,FR-5 | N=2/3 规划 gateway=0 |
| R13 全场景覆盖（完整版推论） | 采用 §7.4 登记表（含 ⚠️ 静态骨架 / ❌ 回落） | FR-1 | 全场景命中矩阵测试 |
| R14 模板版本化可复现 | 采用 FR-22 | FR-22 | diff_config 非空 + 旧 hash 可重放 |
| R15 旁路不能丢 ack/fan-in/租约/hash链/事件 seq（红队 3.4） | 采用 FR-13/15：抽 `_finalize_process` 共享，两路径共用 | FR-13,FR-15 | 无 PEL 重投 / 父会话 COMPLETED / seq 连续 |
| R16 带附件描述（红队 2.4；v1.1 修正） | 采用 FR-19：附件处理全零 LLM，role 明确+ref 存在+能力兼容即确定性编译；仅歧义/缺ref/冲突回落 | FR-19 | role 明确附件 gateway=0；三种回落用例 |
| R35 **两个 widget + 附件**（用户 13:38 原话） | 采用 FR-19 + FR-24：附件事实注入 PlanningFacts，按子任务归属，归属歧义整单回落 | FR-19,FR-24 | N=2+附件拆分用例，附件只进对应子任务，gateway=0 |
| R17 预算触顶（红队 2.5） | 采用 FR-17：compile 前 meter.check | FR-17 | 触顶用例不进沙箱 |
| R18 WidgetSpec 全字段确定（红队 4.2） | 采用 FR-7：16 字段纯函数 + uuid5 | FR-7 | 两次 compile canonical_json 一致 |
| R19 manifest 版本字段口径（红队 4.1） | 采用 FR-9：`deterministic-template:<registry_version>` 等 | FR-9 | check_contract 通过 |
| R20 长尾+显式 grid 边界/ADR-004 硬样本（红队 5.1） | 采用 FR-21：规整说法才命中，route_layer 统计不变 | FR-21 | ADR-004 三条硬样本原样绿 |
| R21 幂等/bundle_ref 形状（红队 2.6/3.6） | 采用 U-10：hash 可复现，bundle 身份每次新 | FR-7,FR-12 | 两次 compile hash 一致 + PG bundle 真 |
| R22 门禁失败体验（红队 5.2） | 采用 FR-18：fail-closed FAILED + error + audit | FR-18 | 失败用例留证齐全 |
| R23 终态事件重复（红队 5.2） | 采用 U-13：复用 artifact 事件，DONE 只标终态 | FR-14 | 事件无重复 bundle 详情 |
| R24 沙箱缓存与实耗记账（红队 3.7） | 采用 FR-11：绕缓存真跑，记实耗 | FR-11,FR-16 | 缓存不短路 + sandbox_minutes>0 |
| R25 澄清重入/resume（红队 2.2/2.3） | 采用 FR-20：resume 不走；grid 取 clarified or original | FR-20 | 两种重入单测 |
| R26 可观测不依赖破损 events schema（红队 4.3） | 采用 FR-23：日志/指标，不改事件 schema | FR-23 | 日志/指标可查 |
| R27 替换现有 LLM 通道 | 否决：诉求是补链路，非替换；替换破坏开放域覆盖 | — | — |
| R28 用假 ModelGateway 实现「不走 LLM」 | 否决：违反模型网关必真实红线 | — | — |
| R29 LLM 首次生成后缓存复用 | 否决：首次仍调 LLM，无法证零调用 | — | — |
| R30 首版只做 3 类静态模板 | 否决：用户明确「不做简单化、不因首版做简单版本」 | — | — |
| R31 为 LLM 通道串 M08 门禁 | 范围外：独立议题 | — | — |
| R32 改 run()/decide()/Deps.model 模型分支 | 范围外：NFR-1 | — | — |
| R33 修 events.schema oneOf 漏判 | 范围外：独立议题，本通道不依赖 | — | — |
| R34 设备 API 真实调用 / 实时外部数据 | 范围外：ADR-010 P-next；仅静态骨架诚实标注 | — | — |

> **二次拷问补漏**：红队复审 10 Blocker 已在 v1.0 全部回填（FR-6~FR-23）。
> **验收可执行性**：R5/R6 的门禁（coverage_check）本轮可执行；R9–R26 的验收为 S3 实现后目标，本轮仅固化口径。
> **无 Open 行**：用户 13:32 授权「具体方案你定」后，原 U-1~U-14 全部落 §10 决策清单，覆盖矩阵无待定项。
