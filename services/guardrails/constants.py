"""M06 具名常量（§6.3 枚举与常量，禁魔法值散落）。"""

from __future__ import annotations

from packages.contracts.enums import GridSpec

# 意图白名单：只放行控件生成（§6.3 INTENT_ALLOWLIST）
INTENT_ALLOWLIST: frozenset[str] = frozenset({"WIDGET_GENERATION"})

# L2 语义路由相似度阈值（§6.3，影子期校准建议初值，版本化可回滚）
SEMANTIC_ROUTE_THRESHOLD = 0.75
# v2.1：样本库扩容（保留 v2 去类型化口径），change_gate 指纹据此触发回归门禁
INTENT_SAMPLES_VERSION = "v2.1"
# L1 语义族词表版本（ADR-004）：进入 change_gate 指纹，扩表必须触发回归门禁
INTENT_VOCAB_VERSION = "v2"

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

# 宿主/产物锚点词表（ADR-004 去类型化）：只回答「有没有承载 widget 的位置或产物」，
# 不再映射到 WidgetType。类型词仅作为普通对象名词的一员，不构成门禁。
WIDGET_ANCHOR_PATTERNS: tuple[str, ...] = (
    r"桌面|主屏|主屏幕|锁屏|负一屏|小组件|小工具|组件|万能卡片|服务卡片|卡片|挂件|面板|Widget|widget|智能叠放|控件|界面|列表|日历",
)

# 关键特征线索词表（§3.2 step2 槽位检查）：描述需有「控件类型 + 关键特征」才算充分。
# 特征词为确定性规则词表，零模型调用；命中任一即认为用户已给出可生成的关键特征。
FEATURE_HINTS: tuple[str, ...] = (
    r"翻月|月视图|月份|周视图|日视图|农历",
    r"计时|倒计时|秒表|表盘|整点|时分秒",
    r"提醒|闹铃|重复|贪睡",
    r"清单|勾选|优先级|截止|完成状态",
    # ADR-004：数据词族开放扩充，仅用于描述充分性与加权，不构成 widget 类型门禁
    r"天气|气温|降雨|空气质量|预报|未来一周|一周",
    r"步数|心率|睡眠|运动|卡路里|体重|喝水",
    r"股票|基金|金价|汇率|行情|涨跌|盈亏",
    r"快递|物流|外卖|航班|路况|公交|地铁",
    r"电量|电池|充电|流量|话费|网速|屏幕时间",
    r"灯光|空调|门锁|窗帘|智能家居|摄像头",
    # 注意：纯外观/动作词（颜色/主题/样式/动画/交互/点击/滑动）不作为独立关键特征，
    # 否则「有样式的玩意」这类无控件信息的描述会误过 R1 槽位检查（review S3）
    r"显示|展示|呈现|列表|卡片|表格",
)

# 密度建议（ADR-004）：按内容特征而非 WidgetType 给建议；未命中用默认顺序。
DENSITY_HINTS: tuple[tuple[str, tuple[GridSpec, ...]], ...] = (
    (r"月视图|翻月|周视图|日历|农历", (GridSpec._4X4, GridSpec._2X4, GridSpec._4X2)),
    (r"多行列表|清单|列表|待办|任务", (GridSpec._4X2, GridSpec._2X4, GridSpec._4X4)),
    (
        r"天气|气温|预报|股票|基金|汇率|行情|快递|物流",
        (GridSpec._4X2, GridSpec._2X4, GridSpec._4X4),
    ),
    (r"时钟|倒计时|计时|闹钟|表盘|电量", (GridSpec._2X2, GridSpec._2X1, GridSpec._4X4)),
    (r"表格|宽表格", (GridSpec._4X2, GridSpec._4X4, GridSpec._2X4)),
)
DEFAULT_DENSITY_HINTS: tuple[GridSpec, ...] = (
    GridSpec._2X2,
    GridSpec._4X4,
    GridSpec._2X4,
)

# 规格冲突组合（§3.4 C 静态矩阵）；feature 为描述特征关键词
CONFLICT_RULES: tuple[tuple[GridSpec, str, str, GridSpec], ...] = (
    (GridSpec._2X1, "月视图", "2x1 无法容纳月视图", GridSpec._4X4),
    (GridSpec._2X1, "多行列表", "2x1 无法容纳多行列表", GridSpec._2X4),
    (GridSpec._1X2, "宽表格", "1x2 无法容纳宽表格", GridSpec._4X2),
)

# ---- L1 语义族（ADR-004 去类型化）----
# 正向 = 生成/放置/展示动作 × 宿主/产物锚点；数据词只加权、永不单独触发。
_WIDGET_ACTION: str = r"(?:做|生成|创建|新建|弄|搞|来个|来一|整个|整一个|加|添加|放|摆|挂|贴|塞|显示|展示|呈现|能看到|看得见)"
_WIDGET_ANCHOR: str = r"(?:桌面|主屏|主屏幕|锁屏|负一屏|小组件|小工具|组件|万能卡片|服务卡片|卡片|挂件|面板|Widget|widget|智能叠放|控件|界面|列表|日历|时钟|闹钟|待办|任务|清单)"
_WIDGET_DATA: str = r"(?:天气|气温|降雨|空气质量|预报|闹钟|闹铃|提醒|倒计时|倒数日|计时|世界时钟|日程|会议|待办|任务|清单|便签|备忘|笔记|步数|心率|睡眠|运动|卡路里|体重|电量|电池|充电|流量|话费|屏幕时间|股票|基金|金价|汇率|行情|涨跌|快递|物流|外卖|打车|航班|高铁|路况|公交|地铁|音乐|播客|照片|相册|壁纸|新闻|灯光|空调|门锁|窗帘|摄像头|扫地机|智能家居|消息|邮件|未读|通话|联系人|日期|时间)"
# 锚点在前时只认弱动作，且排除「怎么添加」中的「加」
_PLACE_AFTER_ANCHOR: str = r"(?:显示|展示|呈现|放|摆|挂|(?<!添)加)"

WIDGET_GENERATION_PATTERNS: tuple[str, ...] = (
    rf"{_WIDGET_ACTION}.{{0,12}}{_WIDGET_ANCHOR}",
    rf"{_WIDGET_ANCHOR}.{{0,12}}{_PLACE_AFTER_ANCHOR}",
    rf"{_WIDGET_DATA}.{{0,6}}{_WIDGET_ANCHOR}",
    rf"{_WIDGET_ACTION}.{{0,8}}{_WIDGET_DATA}.{{0,8}}{_WIDGET_ANCHOR}",
)

# 进护栏没有正向优先：N1/N4 必须自带「有宿主且有生成动词」的联合排除，
# 否则「能帮我在桌面加个天气吗？」会被 check_input 二次误拒（ADR-004 复审 B1）。
_NEG_WIDGET_GUARD: str = (
    r"^(?!(?:.*(?:桌面|主屏|主屏幕|锁屏|负一屏|小组件|小工具|组件|万能卡片|服务卡片|卡片|挂件|面板|Widget|widget|智能叠放|控件|界面|列表)))"
    r"(?!(?:.*(?:做|生成|创建|新建|加|添加|放|摆|挂|显示|展示)))"
)
DEV_CONTEXT_PATTERN: str = (
    r"(?:写|做|开发).{0,12}(?:Widget|widget).{0,8}(?:代码|项目|SDK|API)"
    r"|(?:Android|Swift|Kotlin).{0,8}(?:Widget|小组件)"
)

# L1 负向 8 族（N1–N8）：正向优先在 _l1_rule_intent 中保证合法请求不被误拒。
_N1_QUERY: str = (
    r"(?:今天|现在|明天)?(?:天气|新闻|金价|汇率|股票|快递|步数|电量).{0,8}"
    r"(?:怎么样|如何|多少|几点|到哪|是什么|有哪些|吗|呢|？)"
)
_N4_DEVICE: str = (
    r"(?:(?:把|将).{0,8}(?:灯|空调|窗帘).{0,4}(?:关|开|打开|关闭)"
    r"|播放.{0,8}(?:歌|音乐)|设(?:个|置)?.{0,8}(?:闹钟|提醒)|下单|拨打|导航到)"
)
_N5_TUTORIAL: str = (
    r"(?:小组件|Widget|widget|卡片|组件|控件).{0,8}(?:怎么|如何)(?:添加|用|设置|做)?"
)
_N2_CHAT: str = r"讲(?:个|一)?笑话|写(?:一|首)?首诗|讲讲历史|解释一下|告诉我|跟我说|推荐一?(?:首|部|个)"
_N3_CREATE: str = r"写(?:个|一)?(?:爬虫|脚本|邮件|诗)|写.*(?:通用|后端|爬虫|前端).*代码|翻译(?:这段|一下)"
_N7_BARE: str = r"^(?:天气|新闻|股票|快递|倒计时|电量|闹钟)(?:吗|呢|？)?$"
_N8_STATUS: str = r"屏幕显示(?:无信号|电量不足|错误|已连接)"

OUT_OF_SCOPE_PATTERNS: tuple[str, ...] = (
    _N1_QUERY,
    _N2_CHAT,
    _N3_CREATE,
    _N4_DEVICE,
    _N5_TUTORIAL,
    DEV_CONTEXT_PATTERN,
    _N7_BARE,
    _N8_STATUS,
)

# 进护栏 check_input 没有正向优先：N1/N4 需自带「宿主 + 生成动词」联合排除，
# 否则「能帮我在桌面加个天气吗？」会被二次误拒（ADR-004 复审 B1）。
# 仍从同一批语义族常量派生，不引入第二份手写文本。
INTENT_GUARD_PATTERNS: tuple[str, ...] = (
    _NEG_WIDGET_GUARD + _N1_QUERY,
    _N2_CHAT,
    _N3_CREATE,
    _NEG_WIDGET_GUARD + _N4_DEVICE,
    _N5_TUTORIAL,
    DEV_CONTEXT_PATTERN,
    _N7_BARE,
    _N8_STATUS,
)

# 时间窗口键（§6.1 false_positive_rate）
KEY_FROM = "from"
KEY_TO = "to"
