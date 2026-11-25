# Change: f76a7264-P0P1 / 2026-11-25 / confirmed

## Why
用户 10 项要求拷问实测发现：同一意图被 `validate_chain` 与 `RuleInterpreter` 各判一次且打架（ADR-004 硬样本被 N5 教程族正则误拒），且解释器槽位只有 grid/data/host，数量/样式/交互等 8 类信息静默丢弃。用户拍板 R3 走结构化反问采集、本轮做 P0+P1 止血。

## Delta
- 新增 `docs/adr/ADR-020-0LLM能力边界与结构化反问采集.md`（R3 降级满足定性 + P0/P1 边界）
- 新增 `SlotFill.slots` 显式槽位键：`count`/`style`/`interaction`/`function`/`structure`/`attachment_purpose`/`target`
- 新增 `InterpretResult.missing_slots` 携带具体槽位名 + `question_kind=slot` 反问
- 新增 `check_input` 的意图类正向优先豁免（显式生成动词+宿主+grid，修 ADR-004 硬样本被 N5 误拒；注入/PII 不豁免）
- 删除 `validate_chain.py` 的 step11 `classify_intent` 调用（因意图裁判唯一化而废弃；FR-11 遗留项）
- 新增 `input_guard.classify_intent_mix()`（混排意图语义分类：生成+教程→AWAIT 反问 / 纯教程→越界 / 内容描述→放行）
- 无遗留（未删任何公开符号/字段：`classify_intent` 函数本身保留供其他调用方，仅删 validate_chain 内这一处调用）

## Invariants
- 不改公共 JSON Schema（`pnpm contracts:check` drift 必须为 []）
- 无 DB 迁移（head 仍 0020）
- 同一安全校验只做一次：护栏裁判不重复进解释器，解释器不做放行裁判
- 意图拒绝类仍零模型（L1/INTENT_GUARD_PATTERNS）
- `widget_type` 恒 CUSTOM（ADR-004 去类型化）

## Affected
- `services/orchestrator/validate_chain.py`（删 step11 调用、保留 step0/step7）
- `services/guardrails/constants.py`（INTENT_GUARD_PATTERNS 正向豁免）
- `services/guardrails/interpreter/types.py`、`rule.py`（槽位扩展与反问）
- `services/guardrails/intent_cascade.py`（正向优先口径复用，若需抽公共函数）
- `services/deterministic/channel.py`（反问载荷带槽位名）
- `tests/unit/test_p0p1_intent_slot_clarify.py`（新测试）
- 设计文档：`docs/adr/ADR-004`（硬样本）、`docs/adr/ADR-013` §8（能力边界）、`docs/modules/00-总览与模块地图.md` ADR 索引

## Acceptance
- AC-1：`.venv/Scripts/python.exe -m pytest tests/unit/test_p0p1_intent_slot_clarify.py -q` rc=0
- AC-2：`grep -n classify_intent services/orchestrator/validate_chain.py` 退出码 1（0 命中）
- AC-3：硬样本「桌面放个2x2天气卡片，显示今天天气怎么样」`check_input` passed=True（单测覆盖）
- AC-4：显式说出的 count/style 等槽位进 slots（非泛指「整一个」）；未说出的维度不追加 missing_slots（避免 8 连问死循环），缺 grid 仍返回 clarification（单测）
- AC-5：`.venv/Scripts/python.exe -m pytest -q` rc=0；`npx pyright --pythonpath .venv/Scripts/python.exe` 0 errors；`pnpm contracts:check` drift=[]
- AC-6：ADR-020 存在且含 D1-D4 决策
- AC-7：`classify_intent_mix('桌面放个2x2天气卡片，卡片怎么添加到桌面')` → clarification 且带两段；硬样本 → generation；纯教程 → out_of_scope（单测）
- AC-8：混排藏注入仍走 GUARD_INPUT_HIT（安全红线不被 AWAIT 绕过，单测）
- AC-9：混排 + 敏感词/危险调用/预算0 分别走 SENS_*/DANGEROUS_CALL/BUDGET_EXHAUSTED（AWAIT 不绕过，单测）
- AC-10：无 grid 字面混排、教程在前无标点、纯「怎么生成」分别走反问/反问/硬拒（单测）

## Open
- R7 取消在生成 + TSX 合并：Owner=用户，待立项
- R8 二次失败确定性修复、R9 LLM 产物成包：Owner=用户
- 多轮反问交互编排（D3 的运行时部分）：Owner=用户，下一轮
