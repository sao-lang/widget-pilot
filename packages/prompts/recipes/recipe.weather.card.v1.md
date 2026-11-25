---
recipe_id: recipe.weather.card.v1
version: v1
grid: 4x2
token_cost: 180
---

# 天气卡片生成规程

生成符合 `WidgetSpec` 的 TSX/React 天气卡片控件。只允许 `react` 与 `@widget/bridge` 两个 import，默认导出 `Widget` 组件。

## 结构约束
- 顶部一行：地点 + 当前温度。
- 主体：天气图标 + 简述；grid=4x2 信息密度有限，不横向溢出。

## 数据约束
- 天气数据来自 `props.data`（静态数据）；不调实时 API、不访问网络。
- 无数据时展示占位文案，不编造天气结论。
