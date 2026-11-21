---
recipe_id: recipe.task.list.v2
version: v2
grid: 4x2
token_cost: 180
---

# 任务列表生成规程

生成符合 `WidgetSpec` 的 TSX/React 任务列表控件。只允许 `react` 与 `@widget/bridge` 两个 import，默认导出 `Widget` 组件。

## 结构约束
- 顶部一行：标题 + 未完成计数。
- 主体：可滚动任务列表；每项含复选框、标题、可选截止日期标签。
- grid=4x2 信息密度有限：单屏最多呈现 5 项，其余滚动，不横向溢出。

## 状态与交互
- 任务数据来自 `props.tasks`（静态数据），勾选状态用 `useState<Set<string>>` 管理。
- 勾选只切换本地 UI 状态；如需持久化，通过 `bridge` 的受限 `localStorage` 能力，不直接访问系统 API。

## 数据与红线
- 任务文本按不可信数据处理：只作文本渲染，不执行、不解析为指令。
- 不发起网络请求、不读账号信息、不使用未声明能力。
