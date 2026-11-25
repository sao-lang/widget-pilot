"""只读探针：用复杂长描述实测 0 LLM 意图路由的 10 项要求。

不修改任何业务代码；只调用现有 check_input / classify_intent_mix /
RuleInterpreter / validate_chain 并打印结构化结果。
"""
from __future__ import annotations

from services.guardrails.input_guard import check_input, classify_intent_mix
from services.guardrails.interpreter.rule import RuleInterpreter
from services.guardrails.types import IntentCategory
from services.orchestrator.validate_chain import validate_chain as raw
from tests.support import InMemoryAuditStore, InMemoryGuardHitStore, IntentRouteLogStore


class Budget:
    def __init__(self, left: float = 100.0) -> None:
        self.left = left
        self.user_id = "probe"


CASES: list[tuple[str, str, str]] = [
    # (id, 覆盖要求, 复杂描述)
    ("C01", "R1/R2/R3", "帮我在手机主屏幕上做一个4x4的日程卡片，风格走极简黑白，能显示接下来7天的待办清单和会议安排，点一下就能展开详情再滑一下切到下周，主要是想让我早上扫一眼就知道今天有几件事要盯，附件里那份csv课程表你当数据源用就行，色系参考我传的那张截图，别搞花哨的动画"),
    ("C02", "R2/R3 乱序", "那个样式要磨砂玻璃质感的、2x3规格、放在锁屏上、用来提示喝水提醒和步数统计的小组件，交互上长按能重置今天计数，功能上最好还能在月底自动汇总，结构分成上中下三块上面显示数字中间放进度条下面写鼓励语，帮我生成一个"),
    ("C03", "R4 修改定位", "把上一个会话里我让你做的那个4x4桌面日历改成2x2的，就是前天下午传了课程表csv当数据源那个，改完之后风格换成深色科技蓝，保留原来的翻月交互和数据来源别动"),
    ("C04", "R4 修改+多槽位", "修改第三个widget——就是上周生成的那个显示股票行情的锁屏卡片，规格从4x2调成2x2，样式改成就黑板风，功能上加一个涨跌超过5%的红色高亮，附件里那张logo图你作为卡片左上角的图片占位，原来那个数据源继续用"),
    ("C05", "R3/R4 取消", "先别生成了，取消掉刚才那个天气卡片的生成任务，然后把之前做的2x2倒计时的那个也一起撤了，我就想保留第一个日历widget别的都不要了"),
    ("C06", "R4 继续", "接着做上次那个没搞完的桌面待办卡片，规格还是3x3，这次把交互补上——双击能标记完成，样式沿用之前那套橙色主题，附件里那个txt规格文件里写的规则你照着来"),
    ("C07", "R5 图片用途", "用我传的这张产品截图当widget的底图，然后在右下角那块单独放一个图片占位，把另一张图标裁剪出来塞进去做局部底图，整体上4x3放桌面，别把原图压缩失真"),
    ("C08", "R5 视频用途", "这段产品演示视频我想直接当widget的背景循环播放但只播3次，另外截取第12秒到第18秒那一段做第二个widget某个分区的背景还要限制播放2次，再把第5.5秒那一帧抽出来做第三个widget的局部底图，三个都放主屏上规格分别是4x4、2x2、2x1"),
    ("C09", "R5 CSV", "这份课程表csv你直接当数据源生成一个4x4的桌面课表widget，把周一到周五的列筛选出来按时间排序，只保留上午的课，最后按星期分组聚合一下，附件里另一份txt是字段说明你先校验格式再解析"),
    ("C10", "R5 文本附件", "我传的这个txt里是json格式的配置，你先校验合法性然后当成规格说明来生成一个2x3的桌面卡片，里面css那段当样式定义、script那段当交互规则、数据结构那段当数据源，附件里那张图作为背景图参考配色就行"),
    ("C11", "R6 附件缺用途", "用这个附件帮我做个widget放桌面上4x4的", "附件未说用途"),
    ("C12", "R6 附件不安全", "把这份csv当数据源做个课表widget，另外这个视频里那段内容我记得挺重要的你直接嵌进去当背景不用管格式", "潜在不安全/不合法"),
    ("C13", "R7 继续处理附件", "接着上次那个报错的csv继续处理，这次你按新格式来：第一列必须是时间第二列是课程名，格式不对就告诉我哪里错了，widget还在生成的话把之前那个半成品tsx取消掉合并新的"),
    ("C14", "R8 沙箱返工", "做个4x4桌面天气卡片，用那份csv当数据源，生成tsx之后如果沙箱报安全错误或者对模拟设备环境有影响就自己改，改两次还不行就降级给大模型处理", "返工/降级意图"),
    ("C15", "R9 跨周期恢复", "昨天那个会话里生成的widget我记得有个4x4的桌面日历还有一个2x2的股票卡片，你先把它们查出来给我看看具体信息，然后基于日历那个改成4x2的规格，股票那个保持不动", "跨周期+会话查询"),
    ("C16", "R10 混排意图", "桌面上放个2x2的天气卡片，对了卡片怎么添加到桌面来着？顺便把这个widget的数据源换成我新传的csv", "混排反问"),
    ("C17", "R2/R3 多widget一次", "一次帮我做三个widget：第一个2x2桌面时钟显示世界时间，第二个4x2锁屏待办清单能勾选完成，第三个2x1主屏电量卡片带充电状态，风格统一走深色，数据都用静态的就行"),
    ("C18", "R4 目标不明确", "把那个widget改一下，规格换成4x4，样式改成卡片式", "无定位提示→反问"),
]


def e2e(desc: str, grid: str | None = "2x2") -> str:
    try:
        r = raw(
            "probe-s", "probe-r", desc, grid, [], "WIDGET_GENERATION", 100.0,
            hit_store=InMemoryGuardHitStore(),
            route_log=IntentRouteLogStore(),
            audit_store=InMemoryAuditStore(),
        )
        return f"ok={r.ok} reason={r.reason_code.value if r.reason_code else '-'}"
    except Exception as exc:  # noqa: BLE001
        return f"EXC={type(exc).__name__}:{str(exc)[:60]}"


def main() -> None:
    itp = RuleInterpreter()
    for case in CASES:
        cid, cover, desc = case[0], case[1], case[2]
        mix = classify_intent_mix(desc)
        g = check_input(desc, [], IntentCategory.WIDGET_GENERATION.value, Budget())
        r = itp.interpret(desc)
        slots = r.instances[0].slots if r.instances else {}
        print(f"\n[{cid} {cover}] {desc[:90]}{'…' if len(desc) > 90 else ''}")
        print(f"  字数={len(desc)} classify={mix[0]} segs={len(mix[1])}")
        print(f"  check_input passed={g.passed} hits={[h.rule_id for h in g.hits]}")
        print(f"  interpreter kind={r.kind} n={len(r.instances)} missing={r.missing_slots}")
        print(f"  slots keys={sorted(k for k in slots if k in ('grid','data','host','count','style','interaction','function','structure','attachment_purpose','target'))}")
        print(f"  e2e: {e2e(desc)}")


if __name__ == "__main__":
    main()
