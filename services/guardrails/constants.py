"""M06 具名常量（§6.3 枚举与常量，禁魔法值散落）。"""

from __future__ import annotations

from packages.contracts.enums import GridSpec, WidgetType

# 意图白名单：只放行控件生成（§6.3 INTENT_ALLOWLIST）
INTENT_ALLOWLIST: frozenset[str] = frozenset({"WIDGET_GENERATION"})

# L2 语义路由相似度阈值（§6.3，影子期校准建议初值，版本化可回滚）
SEMANTIC_ROUTE_THRESHOLD = 0.75
INTENT_SAMPLES_VERSION = "v1"

# L3 小模型分类单次 token 上限（§6.3）
MAX_CLARIFY_TOKENS = 512

# 单轮能力加载上限（ADR-003 / 五项闸门）
MAX_CAPABILITY_PER_TURN = 2
MAX_CAPABILITY_TOKENS = 2000

# 语义层阈值（§6.3）
ROLE_CLARIFY_THRESHOLD = 0.6
MIN_DESCRIPTION_LEN = 8
EXCERPT_MAX_CHARS = 200
VALIDATE_TIMEOUT_MS = 500

# 误拦截率窗口默认天数（§3.4 E 统计口径）
FPR_WINDOW_DAYS = 30

# 描述线索词表 → GridSpec（§3.4 G explicit_text，规则词表影子期校准）
GRID_HINT_PATTERNS: tuple[tuple[str, GridSpec], ...] = (
    (r"4\s*[x×]\s*4|四行四列|宽\s*4\s*格.{0,4}高\s*4\s*格", GridSpec._4X4),
    (r"2\s*[x×]\s*4|两行四列|二行四列", GridSpec._2X4),
    (r"4\s*[x×]\s*2|四行两列|四行二列", GridSpec._4X2),
    (r"2\s*[x×]\s*2|两行两列|二行二列", GridSpec._2X2),
    (r"2\s*[x×]\s*1|两行一列|二行一列", GridSpec._2X1),
    (r"1\s*[x×]\s*2|一行两列|一行二列", GridSpec._1X2),
)

# 控件类型线索词表（§3.2 step2 槽位检查 + suggest_grid 密度建议）
WIDGET_HINTS: tuple[tuple[str, WidgetType], ...] = (
    (r"日历|月视图|翻月|calendar", WidgetType.CALENDAR),
    (r"时钟|表盘|tick|clock", WidgetType.CLOCK),
    (r"闹钟|提醒|alarm", WidgetType.ALARM),
    (r"待办|任务|清单|task", WidgetType.TASK),
)

# 关键特征线索词表（§3.2 step2 槽位检查）：描述需有「控件类型 + 关键特征」才算充分。
# 特征词为确定性规则词表，零模型调用；命中任一即认为用户已给出可生成的关键特征。
FEATURE_HINTS: tuple[str, ...] = (
    r"翻月|月视图|月份|周视图|日视图|农历",
    r"计时|倒计时|秒表|表盘|整点|时分秒",
    r"提醒|闹铃|重复|贪睡",
    r"清单|勾选|优先级|截止|完成状态",
    # 注意：纯外观/动作词（颜色/主题/样式/动画/交互/点击/滑动）不作为独立关键特征，
    # 否则「有样式的玩意」这类无控件信息的描述会误过 R1 槽位检查（review S3）
    r"显示|展示|呈现|列表|卡片|表格",
)

# 各控件类型的典型密度建议（§3.4 G suggest_grid 补齐，不足 ≤3 项时用）
WIDGET_TYPICAL_GRIDS: dict[WidgetType, tuple[GridSpec, ...]] = {
    WidgetType.CALENDAR: (GridSpec._4X4, GridSpec._2X4, GridSpec._4X2),
    WidgetType.CLOCK: (GridSpec._2X2, GridSpec._2X1, GridSpec._4X4),
    WidgetType.ALARM: (GridSpec._2X1, GridSpec._2X2, GridSpec._1X2),
    WidgetType.TASK: (GridSpec._4X2, GridSpec._2X4, GridSpec._4X4),
    WidgetType.CUSTOM: (GridSpec._2X2, GridSpec._4X4, GridSpec._2X4),
}

# 规格冲突组合（§3.4 C 静态矩阵）；feature 为描述特征关键词
CONFLICT_RULES: tuple[tuple[GridSpec, str, str, GridSpec], ...] = (
    (GridSpec._2X1, "月视图", "2x1 无法容纳月视图", GridSpec._4X4),
    (GridSpec._2X1, "多行列表", "2x1 无法容纳多行列表", GridSpec._2X4),
    (GridSpec._1X2, "宽表格", "1x2 无法容纳宽表格", GridSpec._4X2),
)

# 越界意图 L1 词表（§3.4 B INTENT_GENERIC_CHAT，只放行控件生成）
OUT_OF_SCOPE_PATTERNS: tuple[str, ...] = (
    r"天气|今天.*怎么样|新闻|讲个笑话|聊天",
    r"写.*(通用|后端|爬虫).*代码",
    r"翻译|写一首诗|讲讲历史",
)

# 控件生成 L1 正向词表（高置信直接裁定 WIDGET_GENERATION）
WIDGET_GENERATION_PATTERNS: tuple[str, ...] = (
    r"(做|生成|来|给我|创建).*(控件|组件|日历|时钟|闹钟|待办|任务|清单|卡片|界面|widget|calendar|clock)",
    r"^(日历|时钟|闹钟|待办|任务|清单)",
)

# 时间窗口键（§6.1 false_positive_rate）
KEY_FROM = "from"
KEY_TO = "to"
