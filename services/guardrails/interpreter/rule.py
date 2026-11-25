"""规则解释器（ADR-016 第 1 层）：全局槽位抽取 + 共现消歧。

针对实测痛点：
1. 不切片——分散槽位（grid 在句尾）按最近宿主聚合
2. 共现消歧——「桌面+卡片」是同一 widget 两种叫法，不数成 2
3. 后置 grid 按实例顺序分配，不新增实例

零模型调用；复用现有词表（单一事实源）。
"""
from __future__ import annotations

import json
import re

from services.guardrails.constants import (
    FEATURE_HINTS,
    GRID_HINT_PATTERNS,
    INTENT_GUARD_PATTERNS,
    WIDGET_ANCHOR_PATTERNS,
    WIDGET_GENERATION_PATTERNS,
)
from services.guardrails.interpreter.types import InterpretResult, LayerUsed, SlotFill

# 最长优先（review S3）：词表是单条「桌面|主屏|主屏幕|...」交替，直接按长度
# 拆开重排，保证「主屏幕」在「主屏」之前被匹配，避免拆成两个宿主实例。
_HOST_TERMS = sorted(
    (t for p in WIDGET_ANCHOR_PATTERNS for t in p.split("|") if t and not t.startswith("(")),
    key=len, reverse=True,
)
_HOST_RE = re.compile("|".join(_HOST_TERMS), re.IGNORECASE)
# 同一 widget 的宿主别名：相邻且无连接词时不算两个实例（修 v2 假阳性）
_ALIAS_PAIRS = frozenset({
    ("桌面", "卡片"), ("主屏", "卡片"), ("锁屏", "卡片"),
    ("桌面", "组件"), ("主屏", "组件"), ("桌面", "小组件"),
    ("主屏", "挂件"), ("桌面", "面板"),
})


def _grids(text: str) -> tuple[tuple[int, str], ...]:
    out: list[tuple[int, str]] = []
    for pattern, grid in GRID_HINT_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            out.append((m.start(), getattr(grid, "value", str(grid))))
    return tuple(sorted(out))


def _datas(text: str) -> tuple[tuple[int, str], ...]:
    """返回 (位置, 命中文本)——不是正则 pattern（review B1）。

    FEATURE_HINTS 里 `显示|展示|...|卡片|列表` 这类含宿主/产物词的规则靠
    _is_hostish 过滤，避免把「卡片」当数据槽位污染产物。
    """
    hostish = {"卡片", "列表", "表格", "界面", "显示", "展示", "呈现"}
    out = [
        (m.start(), m.group(0))
        for pattern in FEATURE_HINTS
        for m in re.finditer(pattern, text, re.IGNORECASE)
        if m.group(0) not in hostish
    ]
    out.extend((m.start(), m.group(0)) for m in _LOCAL_DATA.finditer(text))
    return tuple(sorted(set(out)))


def _hosts(text: str) -> tuple[tuple[int, str], ...]:
    return tuple(sorted((m.start(), m.group(0)) for m in _HOST_RE.finditer(text)))


def _is_alias(a: str, b: str) -> bool:
    return (a, b) in _ALIAS_PAIRS or (b, a) in _ALIAS_PAIRS


# 两个宿主词之间若只隔这些修饰字，视为同一实例（负一屏卡片 / 主屏上的万能卡片）
_ALIAS_BRIDGE = re.compile(r"^[的上里中服务万能智能叠放小组件卡片挂件面板]*$")

# 解释器局部负向：创作/总结/编码类动词带宿主+grid 也不得编译（review S1）。
# 不写回 constants.py 公共词表——避免改变 L1 既有统计与 ADR-004 硬样本。
# v2fix：裸词「摘要/总结/概括」会误伤结构槽位（V19「底部摘要三块」），
# 改为创作语境模板——必须是「总结/摘要 + 文档/文章/内容」才判越界。
_LOCAL_NEGATIVE = re.compile(
    r"(?:帮我|请|帮忙)?(?:总结|概括|摘要)(?:一下|下|这|那|这份|这篇|这段|这个|那个)*(?:文档|文章|内容|文字|报告)"
    r"|写(?:个|一|篇)?(?:爬虫|脚本|代码|文章|邮件)|翻译这段|写首诗|讲个笑话"
)
# 解释器局部数据词族（review S4）：FEATURE_HINTS 没有「日期/星期/时间」等基础数据词，
# 只在解释器层补，不写回公共词表避免改变 L1 统计。值必须是描述中真实 token。
# B3（review）：补「日历/任务/日程」——这些既是 host 词也是常见修改目标，
# 不补则「把第二个日历改成 4x4」的 data 槽为空 → 三门禁 failed（生产不可达）。
_LOCAL_DATA = re.compile(
    r"日期|星期|时间|天气|步数|心率|电量|清单|待办|备忘|倒计时|新闻|股票|快递"
    r"|日历|任务|日程"
)

# S1（review）：控件产物名词（排除纯位置词桌面/主屏/锁屏），修改族正向门要求
# 至少命中一个，避免"取消 2x2 桌面的订单"被误编译。
_WIDGET_OBJECT = re.compile(
    r"日历|时钟|闹钟|待办|任务|清单|天气|卡片|组件|小组件|挂件|面板|Widget|widget"
)

# P2：修改/继续/取消意图词族（解释器局部，不写回公共词表避免改变 L1 统计）。
# 顺序敏感：取消 > 继续 > 修改 > 默认生成（「取消生成」不能被「生成」抢先）。
# 裸「改」单字会误伤「改善/改良」，故要求明确后缀或完整动词（review N1′）。
_LOCAL_INTENT_MODIFY = re.compile(r"改成|改为|改一下|改下|改掉|修改|更换|换成|调整|重做")
_LOCAL_INTENT_CONTINUE = re.compile(r"继续|接着|接着做|续做")
_LOCAL_INTENT_CANCEL = re.compile(r"取消|撤销|不做了|停掉|终止")


# ADR-020 D3：显式槽位信号（结构化反问采集）。
# 纪律：只在用户**明确说出**该维度时写入槽位；未提及不追加 missing_slots，
# 否则每条普通描述都会触发 8 连问，形成澄清死循环。
# 值取描述中的真实 token（不臆造取值域），供 compiler/rework 消费。
# v2fix：值模板改为「锚点 + 可选系动词 + 值」，值用非贪婪且禁用标点/连接词，
# 防止跨句吞掉闲聊（V01 style 抽成整段改写过程）与指代词（V20「这两个都要」）。
_EXPLICIT_SLOT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # count 白名单（Round3 B1）：只认「生成动词 + 数量 + 量词」显式语境。
    # 删掉旧的「数量+个+widget」宽松分支——英文 aforementioned/those、
    # 「上述/前面提过的三个widget」等指代永远补不完（黑名单法失败教训）。
    # 合法「做三个widget」「生成两个widget」仍由本分支覆盖。
    ("count", re.compile(
        r"(?:做|生成|放|来个|来一|整个|整|加|弄|摆|挂)\s*(\d+|[二两三四五六七八九十]+)\s*(?:个|块|张|款|组)"
    )),
    ("style", re.compile(r"(?:样式|风格|配色)(?:是|要|用|换成|走)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,8})")),
    ("interaction", re.compile(r"(?:交互|点击|长按|滑动|悬停|双击)(?:是|要|能|上)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,8})")),
    ("function", re.compile(r"(?:功能|作用)(?:是|要|上)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,8})")),
    ("structure", re.compile(r"(?:结构|布局)(?:是|分成|分)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,8})")),
    ("attachment_purpose", re.compile(r"(?:附件|这张图|这个视频|这份表|CSV|文件).{0,4}(?:当|做|作为|用作)([\u4e00-\u9fa5A-Za-z0-9]{2,8})")),
    ("target", re.compile(r"(?:把|将|修改|改).{0,8}(?:第([一二两三四五六七八九十\d]+)个|那个([主屏桌面锁屏负一屏]+))")),
)

# 否定/hedge/指代词黑名单：命中即视为"非显式取值"，不写槽位
_SLOT_VALUE_BLACKLIST = re.compile(
    r"别|不|没|先|其实|这个|那个|这两个|那些|算了|可以但|之后|后来|想还是|要不要"
)


def _explicit_slots(description: str) -> dict[str, str]:
    """显式槽位抽取：先分句，再在单句内取值，过滤否定/指代噪声。

    v2fix：整句贪婪会跨句吞入闲聊与指代词（V01/V20 实测），故按标点切句后
    逐句取首个合法值；黑名单命中即跳过该句，继续找下一句。
    """
    out: dict[str, str] = {}
    sentences = [s.strip() for s in re.split(r"[,，;；。!！?？\n]", description) if s.strip()]
    for name, pattern in _EXPLICIT_SLOT_PATTERNS:
        for sentence in sentences:
            match = pattern.search(sentence)
            if match is None:
                continue
            groups = [g for g in match.groups() if g]
            value = groups[0] if groups else match.group(0)
            if _SLOT_VALUE_BLACKLIST.search(value):
                continue
            # count 额外校验：用完整 description 做前文（跨换行），
            # 拦截「第\n三个」「那样三个」「那个三个」等定位/指代变体。
            if name == "count":
                prefix = description[:description.find(sentence) + match.start()]
                if re.search(r"(?:第|这|那|那样|那个|这些|那些)\s*$", prefix):
                    continue
            out[name] = value
            break
    return out


def _intent_category(description: str) -> str:
    """描述 → 内部 IntentCategory value（v2fix：分句优先，从句不翻主句）。

    根因：整句 `re.search` 让「原来的数据源继续用」「把半成品取消掉」等
    从句翻转整句意图（V04→CONTINUE、V20→CANCEL）。改为先切句，
    主句=含 grid 或控件产物名词且带修改/生成动词的分句，从句只作为辅助。
    """
    # Round2 S3：连接词「之余/然后/顺便/再把/另外」也作分句边界，
    # 否则「做个卡片之余把旧的取消掉」整句既含生成又含取消，取消抢先。
    sentences = [
        s.strip() for s in re.split(
            r"[,，;；。!！?？\n——]|之余|然后|顺便|再把|另外|与此同时|再(?=做个|做一|放个|放一|生成|建个|来个)", description
        ) if s.strip()
    ]
    # 主句优先：含 grid/生成动词的分句才是用户真正要做的事，
    # 「先取消旧的，再做个2x2天气卡片」应判生成而非取消（S3）。
    def is_main(sentence: str) -> bool:
        return bool(
            any(re.search(p, sentence, re.IGNORECASE) for p in WIDGET_GENERATION_PATTERNS)
            or re.search(r"\d\s*[x×]\s*\d|[二两三四五六七八九十]\s*行", sentence)
        )

    main_sentences = [s for s in sentences if is_main(s)]
    ordered = sorted(sentences, key=lambda s: (not is_main(s), sentences.index(s)))
    for sentence in ordered:
        if _LOCAL_INTENT_MODIFY.search(sentence):
            return "WIDGET_MODIFY"
        if _LOCAL_INTENT_CONTINUE.search(sentence):
            return "WIDGET_CONTINUE"
        if _LOCAL_INTENT_CANCEL.search(sentence):
            # 取消词若出现在从句、而主句本身是生成请求，不翻主句（S3）
            if main_sentences and sentence not in main_sentences:
                continue
            return "WIDGET_CANCEL"
    if main_sentences:
        return "WIDGET_GENERATION"
    # 无分句意图时，整句兜底（保持纯取消句可达）
    if _LOCAL_INTENT_CANCEL.search(description):
        return "WIDGET_CANCEL"
    if _LOCAL_INTENT_CONTINUE.search(description):
        return "WIDGET_CONTINUE"
    if _LOCAL_INTENT_MODIFY.search(description):
        return "WIDGET_MODIFY"
    return "WIDGET_GENERATION"


def _anchors(text: str) -> list[str]:
    """宿主词 → 实例锚点；别名/修饰桥接合并（桌面+卡片=1）。"""
    hosts = _hosts(text)
    kept: list[tuple[int, str]] = []
    for pos, word in hosts:
        if kept:
            prev_pos, prev_word = kept[-1]
            gap = text[prev_pos + len(prev_word):pos]
            if _is_alias(prev_word, word) or _ALIAS_BRIDGE.match(gap):
                continue  # 别名或纯修饰词桥接 → 同一实例
        kept.append((pos, word))
    return [w for _, w in kept]


class RuleInterpreter:
    """纯规则解释器；无权重、无模型、无副作用。"""

    def __init__(self, *, threshold: float = 0.8, attachment_tools=None,
                 capability_load_fn=None) -> None:
        self.threshold = threshold
        self.attachment_tools = attachment_tools
        self.capability_load_fn = capability_load_fn

    def interpret(self, description: str, attachments=(), fields=None) -> InterpretResult:
        fields = fields or {}
        grids, datas, anchors = _grids(description), _datas(description), _anchors(description)

        # 附件按需处理：能力目录驱动（REGISTRY 33 能力，与 LLM tools 同源）
        # 优先走 capability_load_fn（支持 frame_at/crop/csv.filter 多步工具链）；
        # 未注入时回退 legacy attachment_tools（兼容简单 mock 场景）。
        from services.guardrails.interpreter.capability_planner import (
            execute_chain,
            plan_attachment,
        )
        attachment_slots: dict[str, object] = {}
        att_missing: tuple[str, ...] = ()
        att_evidence: tuple[str, ...] = ()
        if self.capability_load_fn is not None:
            chain = plan_attachment(description, attachments)
            result = execute_chain(chain, load_fn=self.capability_load_fn)
            if result.ok:
                for text in result.outputs:
                    try:
                        payload = json.loads(text)
                        if isinstance(payload, dict):
                            # 真 tools 结果形状：{handler, extracted:{...}, degraded}
                            body = payload.get("extracted", payload)
                            if not isinstance(body, dict):
                                body = {}
                            for key in ("rows", "palette", "text", "regions",
                                        "derived_refs", "at_seconds", "columns"):
                                if key in body:
                                    attachment_slots[key] = body[key]
                    except (ValueError, TypeError):
                        attachment_slots.setdefault("attachment_output", text)
                if not result.outputs and attachments:
                    att_missing = ("attachment",)
        elif self.attachment_tools is not None:
            from services.guardrails.interpreter.attachment_planner import (
                AttachmentPlanner,
            )
            from services.guardrails.interpreter.attachment_planner import (
                execute as execute_attachments,
            )
            att_plan = AttachmentPlanner().plan(description, attachments)
            att_result = execute_attachments(att_plan, tools=self.attachment_tools)
            attachment_slots = dict(att_result.slots)
            att_missing = att_result.missing_slots
            att_evidence = att_result.evidence

        # 越界/无生成意图：解释器不产拒绝类，返回 clarification 交调用方按 kind 分流。
        # 「总结文档/写爬虫」等负向族即使带宿主+grid 也不得编译放行（review S1）。
        # 正向优先：显式生成动词 + 宿主 + grid 三者齐全时不被问句词误拒
        # （如「桌面放个 2x2 天气卡片，显示今天天气怎么样」是合法生成请求）。
        has_positive = (bool(grids) and bool(anchors)
                        and any(re.search(p, description, re.IGNORECASE)
                                for p in WIDGET_GENERATION_PATTERNS))
        if _LOCAL_NEGATIVE.search(description) or (
            not has_positive
            and any(re.search(p, description, re.IGNORECASE) for p in INTENT_GUARD_PATTERNS)
        ):
            return InterpretResult(kind="clarification", question_kind="intent",
                                   missing_slots=("intent",), layer_used=LayerUsed.RULE,
                                   evidence=("out_of_scope@rule",))
        # P2 #36：修改/继续/取消意图不带生成动词（"把第二个日历改成 4x4"），
        # 但仍需宿主锚点；放宽正向门只让这三类意图族通过，普通疑问仍拒。
        intent_id = _intent_category(description)
        # S1（review）：CANCEL 不进 compiled（不编译、不成包）；MODIFY/CONTINUE
        # 还需显式控件产物名词，避免"取消 2x2 桌面的订单"这类非控件请求被放行。
        modify_family = intent_id in ("WIDGET_MODIFY", "WIDGET_CONTINUE")
        has_widget_object = bool(_WIDGET_OBJECT.search(description))
        has_generation_verb = any(
            re.search(p, description, re.IGNORECASE) for p in WIDGET_GENERATION_PATTERNS
        )
        if not anchors or not (
            has_generation_verb
            or (modify_family and bool(grids) and has_widget_object)
        ):
            return InterpretResult(kind="clarification", question_kind="intent",
                                   missing_slots=("intent",), layer_used=LayerUsed.RULE)

        # 缺 grid：统一追问，不降级模型
        if not grids:
            return InterpretResult(kind="clarification", question_kind="slot",
                                   missing_slots=("grid",), layer_used=LayerUsed.RULE)

        # v2fix：实例数不再用 len(anchors)——宿主/指代词（桌面/日历/widget/
        # 主屏幕/锁屏）会被误算成多个 widget（V01 单 widget 产 5 个）。
        # 新口径：显式 count > 多 grid 分组数 > 1（单个）。
        explicit = _explicit_slots(description)
        count_value = explicit.get("count", "")
        # 修改意图下描述里常同时出现旧规格与新规格（「4x4…换成2x2」），
        # 多 grid 不代表多 widget，只取最后的 grid 作为目标规格。
        effective_grids = grids[-1:] if intent_id in ("WIDGET_MODIFY", "WIDGET_CONTINUE") and len(grids) > 1 else grids
        assert effective_grids, "grids 已在上方缺 grid 分支短路，此处必非空"
        n = max(len(effective_grids), 1)
        if count_value:
            count_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            parsed_count = count_digits.get(count_value, None)
            if parsed_count is None and count_value.isdigit():
                parsed_count = int(count_value)
            if parsed_count is not None and parsed_count > 1:
                n = parsed_count
        host_positions = _hosts(description)  # (pos, word) 用于 data 最近邻
        instances: list[SlotFill] = []
        for i in range(n):
            grid = effective_grids[i][1] if i < len(effective_grids) else effective_grids[-1][1]
            host = anchors[i] if i < len(anchors) else anchors[-1]
            # data 按最近宿主归属（review S4）；不足时留空，不复用尾部 token
            hp = next((p for p, w in host_positions if w == host and p >= 0), -1)
            data = ""
            if datas and hp >= 0:
                # 优先取宿主之后的第一个数据词，再回退到宿主之前的最近词
                after = [d for d in datas if d[0] > hp]
                nearest = after[0] if after else min(datas, key=lambda d: abs(d[0] - hp))
                if abs(nearest[0] - hp) <= 20:
                    data = nearest[1]
            slots: dict[str, object] = {"grid": grid, "data": data, "host": host}
            slots.update(explicit)  # ADR-020 D3：显式槽位（count/style/...）
            slots.update(attachment_slots)  # 附件解析产物进槽位（rows/derived_refs 等）
            instances.append(SlotFill(
                intent_id=intent_id,
                instance_id=f"h:{i}",
                slots=slots,
                missing_slots=att_missing,
                evidence=(f"grid@{effective_grids[i][0] if i < len(effective_grids) else effective_grids[-1][0]}:{grid}",)
                         + att_evidence,
                confidence=1.0,
            ))
        return InterpretResult(kind="compiled", instances=tuple(instances),
                               layer_used=LayerUsed.RULE, confidence=1.0)


__all__ = ["RuleInterpreter"]
