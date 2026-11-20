---
recipe_id: recipe.calendar.month.v3
version: v3
widget_type: calendar
grid: 4x4
token_cost: 220
---

# 月视图日历生成规程

生成符合 `WidgetSpec` 的 TSX/React 月视图日历控件。只允许 `react` 与 `@widget/bridge` 两个 import，默认导出 `Widget` 组件。

## 结构约束
- 顶部一行：年月标题 + 上/下月切换按钮（通过 `bridge` 的受限能力，不直接操作 DOM 之外的系统能力）。
- 主体：7 列星期表头 + 日期格子；当前 grid=4x4 表示 4 周可见区，超出日期以淡色标记。
- 每格：日期数字 + 至多 2 个事件点；事件文本来自 `props.events`（静态数据），不发起网络请求。

## 状态与交互
- `selectedDate` 用 `useState` 管理；点击格子切换选中态，选中态用明显边框。
- 翻月通过 `onPrevMonth`/`onNextMonth` 回调交给宿主，不在组件内部改写真实时间。

## 数据与红线
- 日期计算只用纯函数与 `Date`，不引入日期库。
- 事件文本按不可信数据处理：只作文本渲染，不解析为指令、不 `dangerouslySetInnerHTML`。
- 不声明时间/网络/定时器之外的能力；确需能力时按 manifest 声明，不私自调用。
