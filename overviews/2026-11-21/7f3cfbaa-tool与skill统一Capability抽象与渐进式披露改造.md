# 会话摘要：tool 与 skill 统一 Capability 抽象与渐进式披露改造

## L0 索引卡（续接时先读这一段，≤12 行）
- 场景 / 路径：链【12 提交 → 11/3 写方案+同步文档 → 9 拷问设计 → 5/6 写代码】/ 混合（提交·文档·只读·改码）
- 当前闸门：S0 完成；S1 路由已声明待执行；尚未进入任何写码门禁
- 交付产物：方案文档（ADR，待写）、同步后的 M03/M06/M07/M10 文档（待改）、Capability 实现（待写）
- 未完成项：① 提交基线 ② 方案 ADR ③ 文档同步 ④ 设计拷问 ⑤ 实现+全量回归
- 下一步：按用户指定顺序执行①提交当前代码（排除噪声文件）
- 用户执行顺序硬约束：先提交 → 再写方案文档 → 改其他文档 → 拷问设计 → 最后写代码（不可乱序）

## 环境
- 日期时间：2026-11-21 06:52 GMT+8
- 项目：widgetpilot / 工作区：E:/vsc-workspace/widgetpilot
- OS / Shell：win32 / bash (Git Bash)
- 运行时：Python `.venv/Scripts/python.exe`；Node/pnpm；Docker daemon 可用（compose 文件 docker-compose.dev.yml）
- IDE / Agent：WorkBuddy；主控 conductor

## 场景与 skill
- 场景：执行计划链（见 L0）；当前 ①提交
- 加载的 skill：conductor（主控）、handoff（S0）、grill-design（拷问阶段）、code-quality-review（review 阶段）
- 主控：conductor（唯一流程主控）
- S0 预检降级：preflight.py 报 18 项 PATH 缺失（ruff/pytest/tsc 等），经核为 PATH 探测误报——本项目用 `.venv/Scripts/python.exe -m pytest`、`npx pyright`、项目内 pnpm，命令均可用；不作为阻断。

## S1 路由声明（执行计划）
- 拓扑：链（5 节点，顺序依赖）
- 节点：
  - ①【场景 12/提交】产出：干净基线 commit（工作区在途改造固化）
  - ②【场景 3/补设计 → 文档】产出：统一 Capability + 渐进披露方案 ADR
  - ③【场景 11/文档同步】产出：M03/M06/M07/M10 + 设计方案 §2.4/§11.6/X6/FR-08 口径对齐
  - ④【场景 9/拷问设计，只读】产出：设计问题清单 + 前沿清零结论（闸门：未清零不进⑤）
  - ⑤【场景 5/6/改码】产出：Capability 实现 + TDD + review + 全量回归
- 边/闸门：①→②→③ 顺序；③→④ 文档为拷问输入；④→⑤ 闸门=设计问题清零；⑤ 内含 S4 验证 + S5 独立 review
- 置信度：0.9（用户给出明确顺序算子"先/再/然后/最后"）
- 假设：["①的'先提交代码'指把当前工作区所有在途改造提交为基线，排除 .coverage/.VSCodeCounter/__pycache__ 等噪声；若只想提交特定文件请说"] 
- 待澄清：无（用户已授权三处待拍板点按'最彻底/合理/合适'由 agent 定）

## L1 摘要
### 会话摘要
1. 核查确认 M03 工具调用是断链空壳（registry/invoke 在，生产零注册/零执行链路，run.py:148-152 回灌假 ref）。
2. 核查确认 X6 skill 披露通校验、空内容（need_skill/skill_router/PG 表在，packages/prompts/recipes/ 不存在）。
3. 发现文档三处规程库归属口径打架（skill_router.py:11、M06:241 说 M10 维护；M10:146/266 说 M06/M07 维护）。
4. 用户拍板：tool 与 skill 统一为 Capability 抽象，L0/L1/L2 分级披露，LLM 按需加载；意图路由退为安全门禁。
5. 用户指定执行顺序：先提交 → 写方案文档 → 改其他文档 → 拷问设计 → 最后写代码。

### 关键决策与理由
- 决策：能力暴露从"M06 静态预取决定"转向"LLM 看 L0 目录自主选择" — 理由：解除意图路由与 skill 的因果倒置，落实渐进披露省常驻税的精神。
- 决策（agent 拍板，三项）：① L0 目录放 system prompt 固定槽位保 caching；② `_PREFETCH_MAP` 降为 L0 白名单来源+默认建议、正文加载权交 LLM；③ L2 加载统一计入 X4 四维预算（防绕过）。

### 未完成项与下一步（完整）
- 见 L0「未完成项」；suggested skills：grill-design（拷问）、code-quality-review（review）
