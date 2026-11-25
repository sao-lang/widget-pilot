# 需求覆盖矩阵 · 意图 Schema 驱动槽位编译器 v2.0

> 需求列来自用户原话（本轮 + 前几轮），逐条抄。规格：`f76a7264-意图schema槽位编译器v2.spec.md`。

| 需求 | 决策 | FR | 验收 |
|---|---|---|---|
| R1 「专门用于生成手机 widget 的 agent」 | 采用：schema 只覆盖 widget 意图族，不接开放域 | FR-1 | 8 族 schema 自校验 |
| R2 「输入包括文字性描述和附件，附件分为视频 图片 csv 文本文件等，可用于 widget 创造」 | 采用 FR-6：四类附件纯代码解析成 typed slot | FR-6 | 四类附件填对槽位、gateway=0 |
| R3 「用户一次可以生成多个 widget」 | 采用 FR-7：编译层一次识别多意图实例，DeterministicPlanLLM 拆子任务 | FR-7 | 2 widget fan-out 各自成包 |
| R4 「期望意图识别尽量在复杂用户描述中精准命中不走llm路径，直接根据解析出来的要求生成widget」 | 采用方案 A：schema 驱动前置编译 | FR-3,FR-4,FR-11 | 复杂描述矩阵 + gateway=0 |
| R5 「我说你做的太薄，是因为【桌面上加个2x2显示今天日期的卡片】太简单了」 | 采用：命中率由 schema 覆盖度而非词表决定 | FR-1,FR-4 | 8 族 schema + 复杂长句双 widget 抽取 |
| R6 「你可以先调研下互联网大厂的方案」 | 采用：A2UI/App Schemas/Snips/文档抽取四源调研 | FR-1（调研结论落到 schema 驱动设计） | §2 四源结论各映射到 ≥1 条 schema 决策 |
| R7 「复杂一点的，不就命中不了了。这个设计做的就太薄了呀」 | 采用 v2 重构，否决 v1 加词表路线 | FR-1~FR-7 | §6 Approaches A 采用 / B 否决 |
| R8 用户拍板「纯规则 + 完整 schema + 附件解析（严格零 LLM）」 | 采用 FR-3/NFR-1，否决本地小模型与纯 LLM | FR-3 | import 图零模型端口 |
| R9 用户拍板「重写设计到 v2.0 并出 ADR-015」 | 采用：本规格 + ADR-015 落盘 | FR-13（设计交付含归因与版本可追溯） | 文件存在 + 门禁 PASS |
| R10 用户问「意图识别改造后放到什么位置？还是放在校验链中吗？」 | 采用 FR-8：提出来作前置层，不留在校验链里 | FR-8 | 顺序断言：护栏后、语义层前 |
| R11 用户问「校验链中如果信息不足会做澄清...澄清在意图识别之前还是之后？」 | 采用：源码核实答「劈两半」；v2 统一为 slot dialog 追问 | FR-9,FR-10 | 一次追问用例 + 语义层无重复抽取 |
| R12 用户确认位置方案「可以」 | 采用 FR-8/9/10 按确认执行 | FR-8,FR-9,FR-10 | 同 R10/R11 |
| R13 schema 版本化与门禁指纹 | 采用 FR-2 | FR-2 | change_gate diff_config 非空 |
| R14 实体归一化（时间/颜色/数值/grid） | 采用 FR-5 | FR-5 | 「下周一」「四宫格」归一用例 |
| R15 多 widget 各自缺槽位独立追问，不整单阻塞 | 采用 FR-7 | FR-7 | 2 widget 一个缺槽位的用例 |
| R16 编译结果过同款三门禁与真成包 | 采用 FR-11（复用 ADR-013） | FR-11 | 真 podman + PG bundle |
| R17 拒绝类零模型（I14）与 ADR-004 硬样本 | 采用 FR-12：L1 仍最前短路 | FR-12 | 三条硬样本原样绿 |
| R18 编译可观测归因 | 采用 FR-13 | FR-13 | 日志/指标可查 |
| R19 schema 未覆盖的意图怎么办 | 采用（14:15 澄清）：编译能力不足→**降级 LLM 通道**（理解层零 LLM） | FR-16,FR-17 | 自由表达走 model.complete + generation_path=llm-fallback |
| R20 意图族覆盖度与命中率量化 | 采用（红队 5.1/5.2）：11 族对齐 ADR-004 + 命中率门槛（U-5） | FR-1 | 11 族自校验 + 24 条复杂样例实测：直接≥70%/含追问≥85% |
| R26 视频附件要求「提取文字」而非关键帧 | 采用（红队 1.1）：role×kind 映射矩阵，`process_video().regions` 进 text slot | FR-6 | 视频 OCR 用例填对槽位 |
| R27 CSV 只有表头没数据 | 采用（红队 1.2）：row_count=0 → slot missing → dialog | FR-6 | 空 CSV 进 missing |
| R28 role 歧义 ≠ slot 缺失，追问文案不能丢 | 采用（红队 1.3）：question_kind ∈ {slot,role}，保留 `_ROLE_HINT` | FR-9 | role 追问文案断言 |
| R29 多 widget 一个缺槽位，其他不重复生成 | 采用（红队 1.4）：SlotFill 按 instance_id 持久化 | FR-7 | 补答后只生成缺失实例 |
| R30 编译层与 step11 双意图结论冲突 | 采用（红队 2.1/2.2/2.3）：删 step11 classify_intent，理解权归编译层；COMPILER 层枚举 | FR-12 | grep 无生产调用 + ADR-004 硬样本绿 |
| R31 SlotFill 无下游传递载体 | 采用（红队 2.4）：ValidationResult/SemanticResult/PlanningFacts/Input 加内部字段 | FR-11 | 字段可从编译追到构造器 |
| R32 严格零 LLM 但 step11 可注入模型端口 | 采用（红队 3.1）：删 validate_chain 三端口参数与 L2/L3/L4 注入 | FR-12 | import 图无端口 + 生产装配断言 |
| R33 SPEC_CONFLICT 靠重抽 feature 与「禁止重抽」矛盾 | 采用（红队 4.1）：feature 为编译层 slot，语义层只读判定 | FR-9 | 冲突仍能判 + 无重复抽取 |
| R34 成功标准依赖未落地的 ADR-013 | 采用（红队 5.3）：标注 S3 后置验收 | FR-11（依赖 ADR-013 构造器与成包链） | §3 前置依赖声明 + S3 后置验收 |
| R21 用本地小模型做槽位填充 | 否决：用户拍板严格零 LLM | — | — |
| R22 纯 LLM 理解 | 否决：与产品定位冲突 | — | — |
| R23 继续 v1 加词表路线 | 否决：用户明确「太薄」 | — | — |
| R24 改公共契约 schema 或 run()/decide() | 范围外：NFR-2 | — | — |
| R25 设备 API 真实调用 / 实时外部数据 | 范围外：ADR-010 P-next | — | — |

> 红队复审（模式切换）发现 6 Blocker 已全部回填：R30/R31/R32（双意图仲裁、SlotFill 载体、零 LLM 端口）、R19（U-4 经用户 14:15 澄清改为 LLM 降级）、R33（冲突归属）、R29（多实例追问状态）、R34（依赖标注）。U-1/U-2 用户已拍板、U-3 已确认；U-4 经用户 14:15 澄清为降级 LLM、U-5 为 11 族覆盖。
> 验收可执行性：R6/R9 的门禁本轮已执行（coverage_check PASS）；R13~R34 的 S3 验收目标依赖 ADR-013 落地，本轮只固化口径。
