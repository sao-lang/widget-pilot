"""生成 widget 描述样本库 JSON（设计资产，可重复生成）。

样本从「用户场景」角度组织，不按 widget 类型分（ADR-004 去类型化）。
每条样本标注期望的「描述成分」——意图/操作/目标/宿主/规格/数据/样式/附件使用/歧义。
"""
from __future__ import annotations

import json
from pathlib import Path

# 成分维度（封闭集合，见 ADR-017）：
# intent: create | modify | delete | clarify_answer | out_of_scope
# operation: set | add | remove | replace | style | reorder | none
# target: new | last | ordinal | by_host | by_content | all | none
# host: desktop | lock | home | neg1 | none
# grid: 2x2 | 4x4 | ... | unspecified
# data: time | static | local | attachment | external | none
# style: palette | background | font | radius | theme | none
# attach_role: data_source | style_ref | background | content | rule | none
# ambiguity: none | missing_grid | role_unclear | target_unclear | conflict

S: list[dict] = []


def add(sid: str, scene: str, text: str, **components) -> None:
    S.append({"id": sid, "scene": scene, "text": text, "components": components})


# --- A. 新建·纯文字（简单/复杂/口语） --------------------------------------
add("A01", "新建·规整", "桌面上加个 2x2 的日期卡片",
    intent="create", operation="none", target="new", host="desktop", grid="2x2",
    data="time", style="none", attach_role="none", ambiguity="none")
add("A02", "新建·复杂单句", "帮我做一个锁屏上的 4x4 的小组件，用来显示今天的日期和星期，风格简洁一点",
    intent="create", operation="none", target="new", host="lock", grid="4x4",
    data="time", style="theme", attach_role="none", ambiguity="none")
add("A03", "新建·分散槽位", "我在桌面上想要一个能看到倒计时的卡片，尺寸就 2x2 吧，倒计时到本周五晚上八点",
    intent="create", operation="none", target="new", host="desktop", grid="2x2",
    data="time", style="none", attach_role="none", ambiguity="none")
add("A04", "新建·口语长尾", "给我整一个能显示天气的小玩意，2x2 的，放主屏",
    intent="create", operation="none", target="new", host="home", grid="2x2",
    data="static", style="none", attach_role="none", ambiguity="none")
add("A05", "新建·缺规格", "帮我在桌面做个显示日期的小组件",
    intent="create", operation="none", target="new", host="desktop", grid="unspecified",
    data="time", style="none", attach_role="none", ambiguity="missing_grid")
add("A06", "新建·多widget", "桌面上加个时钟和待办清单，都是 2x2",
    intent="create", operation="none", target="new", host="desktop", grid="2x2",
    data="time+local", style="none", attach_role="none", ambiguity="none", instances=2)
add("A07", "新建·后置规格", "我想要在桌面有个显示日期的大卡片，另外锁屏也放一个倒计时，规格分别 4x4 和 2x1",
    intent="create", operation="none", target="new", host="desktop+lock", grid="4x4+2x1",
    data="time", style="none", attach_role="none", ambiguity="none", instances=2)
add("A08", "新建·带约束", "做个 4x4 的待办清单，最多显示 5 条，超过就折叠，字体大一点",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="local", style="font", attach_role="none", ambiguity="none", constraint="max5+fold")

# --- B. 修改·现有 widget（用户点出的缺口） ---------------------------------
add("B01", "修改·改规格", "把刚才那个卡片改成 4x4 的",
    intent="modify", operation="set", target="last", host="none", grid="4x4",
    data="none", style="none", attach_role="none", ambiguity="none")
add("B02", "修改·按宿主定位", "锁屏那个倒计时换成 2x1",
    intent="modify", operation="set", target="by_host", host="lock", grid="2x1",
    data="none", style="none", attach_role="none", ambiguity="none")
add("B03", "修改·按内容定位", "显示天气的那个卡片改用这个 CSV 数据源",
    intent="modify", operation="replace", target="by_content", host="none", grid="none",
    data="attachment", style="none", attach_role="data_source", ambiguity="none")
add("B04", "修改·改样式", "桌面那个清单背景换成我传的这张图，圆角大一点",
    intent="modify", operation="style", target="by_host", host="desktop", grid="none",
    data="none", style="background+radius", attach_role="background", ambiguity="none")
add("B05", "修改·增加元素", "在刚才的卡片里再加个显示电量的小格",
    intent="modify", operation="add", target="last", host="none", grid="none",
    data="static", style="none", attach_role="none", ambiguity="none")
add("B06", "修改·删除元素", "把清单里天气那栏去掉",
    intent="modify", operation="remove", target="by_content", host="none", grid="none",
    data="none", style="none", attach_role="none", ambiguity="none")
add("B07", "修改·代词指代", "把它改成深色主题，另外字号调小",
    intent="modify", operation="style", target="last", host="none", grid="none",
    data="none", style="theme+font", attach_role="none", ambiguity="target_unclear")
add("B08", "修改·全部", "所有卡片都改成 2x2，配色统一用蓝色",
    intent="modify", operation="set", target="all", host="none", grid="2x2",
    data="none", style="palette", attach_role="none", ambiguity="none")
add("B09", "修改·序号定位", "第二个卡片的数据源换成新传的表格",
    intent="modify", operation="replace", target="ordinal", host="none", grid="none",
    data="attachment", style="none", attach_role="data_source", ambiguity="none")
add("B10", "修改·多轮累积", "刚才那个再加个倒计时，然后背景用第一张图",
    intent="modify", operation="add+style", target="last", host="none", grid="none",
    data="time", style="background", attach_role="background", ambiguity="none")

# --- C. 附件·数据源 --------------------------------------------------------
add("C01", "附件·CSV数据源", "桌面上放个 4x4 的清单，用我刚传的 CSV 当数据源",
    intent="create", operation="none", target="new", host="desktop", grid="4x4",
    data="attachment", style="none", attach_role="data_source", ambiguity="none")
add("C02", "附件·CSV筛选", "用 CSV 里 date 列等于 2026 的行做个 2x2 的日期卡片",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="attachment", style="none", attach_role="data_source", ambiguity="none",
    constraint="filter:date=2026")
add("C03", "附件·CSV字段选择", "从表格里挑 name 和 price 两列，做 4x2 的商品清单",
    intent="create", operation="none", target="new", host="none", grid="4x2",
    data="attachment", style="none", attach_role="data_source", ambiguity="none",
    constraint="select:name,price")
add("C04", "附件·CSV排序统计", "按 CSV 的 score 降序排，取前 5 行做卡片",
    intent="create", operation="none", target="new", host="none", grid="unspecified",
    data="attachment", style="none", attach_role="data_source", ambiguity="missing_grid",
    constraint="sort:score desc limit5")
add("C05", "附件·空数据", "用这个只有表头没数据的 CSV 做个卡片",
    intent="create", operation="none", target="new", host="none", grid="unspecified",
    data="attachment", style="none", attach_role="data_source", ambiguity="missing_grid")

# --- D. 附件·样式与背景 ----------------------------------------------------
add("D01", "附件·配色参考", "按这张截图的配色做个 4x4 的待办清单",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="local", style="palette", attach_role="style_ref", ambiguity="none")
add("D02", "附件·图片背景", "把我传的这张图作为控件背景，做个 2x2 的桌面卡片",
    intent="create", operation="none", target="new", host="desktop", grid="2x2",
    data="static", style="background", attach_role="background", ambiguity="none")
add("D03", "附件·视频帧背景", "用视频第 3.5 秒的帧做 2x2 卡片的背景",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="static", style="background", attach_role="background", ambiguity="none",
    constraint="frame_at:3.5")
add("D04", "附件·裁帧背景", "取视频第 5 秒的帧，裁出中间区域当背景，做个 4x4 卡片",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="static", style="background", attach_role="background", ambiguity="none",
    constraint="frame_at:5+crop:center")
add("D05", "附件·风格+布局参考", "照这个截图的布局和风格，做个 4x4 的桌面卡片",
    intent="create", operation="none", target="new", host="desktop", grid="4x4",
    data="none", style="palette+layout", attach_role="style_ref", ambiguity="none")

# --- E. 附件·内容提取 ------------------------------------------------------
add("E01", "附件·OCR文字", "把图片里识别出的文字显示在 2x2 卡片上",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="attachment", style="none", attach_role="content", ambiguity="none")
add("E02", "附件·视频OCR", "把视频第 2 秒和第 4 秒的画面文字显示出来，做个 2x2 卡片",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="attachment", style="none", attach_role="content", ambiguity="none",
    constraint="frames_ocr:2,4")
add("E03", "附件·文档规则", "按我传的文档里的规则显示待办，红色表示紧急，做个 4x4 卡片",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="attachment", style="palette", attach_role="rule", ambiguity="none")
add("E04", "附件·文本统计", "统计这个文本文件的字数和行数，放到 2x2 卡片里",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="attachment", style="none", attach_role="content", ambiguity="none")

# --- F. 多附件组合 ---------------------------------------------------------
add("F01", "多附件·数据+样式", "用 CSV 当数据源，截图当配色，做个 4x4 的桌面清单",
    intent="create", operation="none", target="new", host="desktop", grid="4x4",
    data="attachment", style="palette", attach_role="data_source+style_ref",
    ambiguity="none", attachments=2)
add("F02", "多附件·帧+数据+背景", "视频第 3 秒的帧做背景，CSV 的数据填进去，做个 2x2 卡片",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="attachment", style="background", attach_role="background+data_source",
    ambiguity="none", attachments=2, constraint="frame_at:3")
add("F03", "多附件·归属歧义", "用这两个表的数据和图做卡片",
    intent="create", operation="none", target="new", host="none", grid="unspecified",
    data="attachment", style="none", attach_role="unclear", ambiguity="role_unclear",
    attachments=3)
add("F04", "多附件·多widget分配", "桌面放个清单用 CSV，锁屏放个倒计时用那张截图当背景",
    intent="create", operation="none", target="new", host="desktop+lock", grid="unspecified",
    data="attachment", style="background", attach_role="data_source+background",
    ambiguity="none", instances=2, attachments=2)

# --- G. 澄清回答 -----------------------------------------------------------
add("G01", "澄清·grid", "4x4",
    intent="clarify_answer", operation="none", target="none", host="none", grid="4x4",
    data="none", style="none", attach_role="none", ambiguity="none")
add("G02", "澄清·role", "这张图当配色参考",
    intent="clarify_answer", operation="none", target="none", host="none", grid="none",
    data="none", style="palette", attach_role="style_ref", ambiguity="none")
add("G03", "澄清·目标", "第二个",
    intent="clarify_answer", operation="none", target="ordinal", host="none", grid="none",
    data="none", style="none", attach_role="none", ambiguity="none")

# --- H. 越界/拒绝 ----------------------------------------------------------
add("H01", "越界·查询", "今天天气怎么样",
    intent="out_of_scope", operation="none", target="none", host="none", grid="none",
    data="none", style="none", attach_role="none", ambiguity="none")
add("H02", "越界·开发语境", "用 Swift 写个 widget 项目",
    intent="out_of_scope", operation="none", target="none", host="none", grid="none",
    data="none", style="none", attach_role="none", ambiguity="none")
add("H03", "越界·需实时", "做个 2x2 的实时天气卡片，显示上海现在的天气",
    intent="out_of_scope", operation="none", target="new", host="none", grid="2x2",
    data="external", style="none", attach_role="none", ambiguity="none")
add("H04", "越界·需模型推理", "帮我总结下这个文档然后放到 2x2 卡片里",
    intent="out_of_scope", operation="none", target="new", host="none", grid="2x2",
    data="none", style="none", attach_role="none", ambiguity="none")

# --- I. 歧义与冲突 ---------------------------------------------------------
add("I01", "歧义·宿主别名", "桌面上的卡片放个 2x2 的日期",
    intent="create", operation="none", target="new", host="desktop", grid="2x2",
    data="time", style="none", attach_role="none", ambiguity="none", note="桌面+卡片=1实例")
add("I02", "歧义·目标不明", "把它改一下",
    intent="modify", operation="set", target="last", host="none", grid="none",
    data="none", style="none", attach_role="none", ambiguity="target_unclear")
add("I03", "冲突·规格与特征", "做个 4x4 的月视图日历卡片",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="time", style="none", attach_role="none", ambiguity="conflict")


# --- J. 扩充·复杂组合（把维度交叉打满） ------------------------------------
add("J01", "新建·三widget混合", "桌面放个 2x2 的时钟，主屏来个 4x4 的待办清单，锁屏再加一个 1x2 的电量卡片",
    intent="create", operation="none", target="new", host="desktop+home+lock",
    grid="2x2+4x4+1x2", data="time+local+static", style="none", attach_role="none",
    ambiguity="none", instances=3)
add("J02", "新建·样式全套", "做个 4x4 的圆角卡片，深蓝渐变背景，白色大字体，主屏桌面都能放",
    intent="create", operation="none", target="new", host="home+desktop", grid="4x4",
    data="none", style="palette+background+font+radius", attach_role="none", ambiguity="none")
add("J03", "新建·条件规则", "做个清单，早上显示今天的待办，晚上显示完成统计，超过 10 条就分页，2x2",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="time+local", style="none", attach_role="none", ambiguity="none",
    constraint="time-conditional+pagination")
add("J04", "修改·规格+样式+指代", "把锁屏那个 2x1 的倒计时改成 4x2，背景换成刚传的图，字体再粗一点",
    intent="modify", operation="set+style", target="by_host", host="lock", grid="4x2",
    data="none", style="background+font", attach_role="background", ambiguity="none")
add("J05", "修改·多目标分别改", "第一个卡片改成 4x4，第二个换成深色，第三个数据源用新 CSV",
    intent="modify", operation="set+style+replace", target="ordinal", host="none", grid="4x4",
    data="attachment", style="theme", attach_role="data_source", ambiguity="none", instances=3)
add("J06", "修改·隐式目标", "这个卡片太挤了，弄大点，颜色也换个暖色的",
    intent="modify", operation="set+style", target="last", host="none", grid="unspecified",
    data="none", style="palette", attach_role="none", ambiguity="target_unclear")
add("J07", "修改·撤销", "撤销刚才对桌面卡片的修改",
    intent="modify", operation="revert", target="by_host", host="desktop", grid="none",
    data="none", style="none", attach_role="none", ambiguity="none")
add("J08", "附件·多帧+文字", "取视频 1 秒和 3 秒的画面，识别里面的文字，配上传的 CSV 做 4x4 卡片",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="attachment", style="none", attach_role="content+data_source", ambiguity="none",
    attachments=2, constraint="frames_ocr:1,3")
add("J09", "附件·图片裁剪做背景", "把那张截图左上角裁出来当背景，做个 2x2 的桌面卡片，配色也按它来",
    intent="create", operation="none", target="new", host="desktop", grid="2x2",
    data="none", style="background+palette", attach_role="background+style_ref",
    ambiguity="none", constraint="crop:left-top")
add("J10", "附件·视频探测", "先看看这个视频多长、多少帧，然后取最后一秒的帧做 2x2 卡片背景",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="none", style="background", attach_role="background", ambiguity="none",
    constraint="probe+frame_at:last")
add("J11", "附件·文档清洗分块", "把文本分块后每块显示一条，做个 4x4 清单，去掉空行",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="attachment", style="none", attach_role="content", ambiguity="none",
    constraint="clean+chunk")
add("J12", "附件·两图对比", "对比这两张截图的差异，把不同的地方列成 4x4 卡片",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="attachment", style="none", attach_role="content", ambiguity="none",
    attachments=2, constraint="vision.compare")
add("J13", "附件·规则源+数据", "按文档里的优先级规则给 CSV 的行染色，做个 4x4 待办",
    intent="create", operation="none", target="new", host="none", grid="4x4",
    data="attachment", style="palette", attach_role="rule+data_source", ambiguity="none",
    attachments=2)
add("J14", "新建·长句混合", "帮我整一个那种能放在主屏幕上的、看起来像玻璃质感的小组件，尺寸 2x2，里面显示日期、星期和倒计时，倒计时到年底，然后配色用我刚发的那张图",
    intent="create", operation="none", target="new", host="home", grid="2x2",
    data="time", style="palette+theme", attach_role="style_ref", ambiguity="none")
add("J15", "修改·多轮复合", "把桌面的卡片改成 4x4，然后删掉天气那栏，再加个显示步数的，最后背景用视频第 2 秒的帧",
    intent="modify", operation="set+remove+add+style", target="by_host", host="desktop",
    grid="4x4", data="attachment", style="background", attach_role="background",
    ambiguity="none", constraint="frame_at:2")
add("J16", "新建·静态文案", "做个 2x2 的卡片，上面就写「今天也要加油」，白底黑字圆角",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="static", style="palette+radius", attach_role="none", ambiguity="none")
add("J17", "新建·数据全本地", "2x2 的待办列表，能勾选完成，按优先级排序，截止日期红色显示",
    intent="create", operation="none", target="new", host="none", grid="2x2",
    data="local", style="palette", attach_role="none", ambiguity="none")
add("J18", "歧义·附件没说明", "用我传这些东西做个卡片",
    intent="create", operation="none", target="new", host="none", grid="unspecified",
    data="attachment", style="none", attach_role="unclear", ambiguity="role_unclear",
    attachments=3)
add("J19", "越界·设备真实调用", "做个 2x2 的卡片能直接控制我家里的灯开关",
    intent="out_of_scope", operation="none", target="new", host="none", grid="2x2",
    data="external", style="none", attach_role="none", ambiguity="none")
add("J20", "越界·实时行情", "主屏放个 4x4 的卡片显示股票实时涨跌和金价",
    intent="out_of_scope", operation="none", target="new", host="home", grid="4x4",
    data="external", style="none", attach_role="none", ambiguity="none")


def main() -> None:
    out = {
        "version": "v1.0",
        "generated": "2026-11-25",
        "taxonomy": {
            "intent": ["create", "modify", "delete", "clarify_answer", "out_of_scope"],
            "operation": ["none", "set", "add", "remove", "replace", "style", "reorder", "revert"],
            "target": ["new", "last", "ordinal", "by_host", "by_content", "all", "none"],
            "host": ["desktop", "lock", "home", "neg1", "none"],
            "data": ["time", "static", "local", "attachment", "external", "none"],
            "style": ["palette", "background", "font", "radius", "theme", "layout", "none"],
            "attach_role": ["data_source", "style_ref", "background", "content", "rule", "unclear", "none"],
            "ambiguity": ["none", "missing_grid", "role_unclear", "target_unclear", "conflict"],
        },
        "samples": S,
    }
    path = Path(__file__).parent / "widget-description-corpus.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(S)} samples to {path}")


if __name__ == "__main__":
    main()
