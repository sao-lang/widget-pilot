"""M03 partial-JSON 容错解析（§11.5 / §4.1 L6）。

流式用容错解析补齐未闭合字符串与括号，只产出 PartialObject；不稳定字段
不触发任何副作用（I9）。仅 final 由 schema.validate 严格校验。
"""
from __future__ import annotations

import json
import re

from packages.agent_core.types import JsonObject, PartialObject


def _close_json(text: str) -> str:
    """补齐未闭合的字符串 / 数组 / 对象，使片段可被 json.loads 解析。

    策略：扫描时跟踪是否在字符串内、忽略转义字符，记录未闭合的
    `[`/`{`；末尾若在字符串内先补 `"`，再按栈逆序补闭合符。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    stack: list[str] = []
    for ch in text:
        # 逐字符扫描：先分流字符串内/外，避免把括号、引号误判
        if in_string:
            # 字符串内部只关心转义与结束引号，其余原样保留
            out.append(ch)
            if escaped:
                # 当前字符已被转义，消费后清除标志
                escaped = False
            elif ch == "\\":
                # 反斜杠使下一个字符转义
                escaped = True
            elif ch == '"':
                # 未转义的引号闭合字符串
                in_string = False
            continue
        if ch == '"':
            # 字符串外的引号开启新字符串
            in_string = True
            out.append(ch)
        elif ch in "[{":
            # 左括号入栈，记录需在末尾补的右括号
            stack.append("]" if ch == "[" else "}")
            out.append(ch)
        elif ch in "]}":
            # 右括号与栈顶配对出栈，保持括号栈平衡
            if stack:
                # 栈非空才弹，避免多余右括号导致索引错误
                stack.pop()
            out.append(ch)
        else:
            out.append(ch)
    if in_string:
        # 扫描结束仍在字符串内，补一个收尾引号
        out.append('"')
    # 若末尾是未完成的 key: value（如 {"a": ），去掉悬空冒号避免非法 JSON
    s = "".join(out)
    s = re.sub(r":\s*$", ": null", s)
    s += "".join(reversed(stack))
    return s


def parse(text: str) -> PartialObject:
    """容错解析：能解析则 complete=True，否则返回尽力构造的 dict（complete=False）。"""
    try:
        value = json.loads(text)
        return PartialObject(value=value if isinstance(value, dict) else {"_value": value},
                             complete=True, raw=text)
    except (json.JSONDecodeError, TypeError):
        pass
    closed = _close_json(text)
    try:
        value = json.loads(closed)
        # 原始 text 未真正闭合 → 标记为 partial，即便补齐后能解析
        complete = text.rstrip().endswith(("}", "]")) and _balanced(text)
        return PartialObject(value=value if isinstance(value, dict) else {"_value": value},
                             complete=complete, raw=text)
    except (json.JSONDecodeError, TypeError):
        # 仍失败（如开头就不是 {）：尽力提取 key-value 对
        return PartialObject(value=_best_effort(text), complete=False, raw=text)


def _balanced(text: str) -> bool:
    """判断括号与字符串是否完整闭合，供 parse 判定原本是否已是完整 JSON。"""
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        # 与 _close_json 同款状态机，但只统计闭合情况、不改写文本
        if in_string:
            # 字符串内：只处理转义与结束引号
            if escaped:
                # 转义字符已消费，重置标志
                escaped = False
            elif ch == "\\":
                # 反斜杠开启下一次转义
                escaped = True
            elif ch == '"':
                # 未转义引号结束字符串
                in_string = False
        elif ch == '"':
            # 字符串外的引号开启字符串
            in_string = True
        elif ch in "[{":
            # 左括号增加嵌套深度
            depth += 1
        elif ch in "]}":
            # 右括号减少嵌套深度
            depth -= 1
    return depth == 0 and not in_string


def _best_effort(text: str) -> JsonObject:
    """尽力从非法文本中提取 key-value 对（结构不确定，用 JsonObject 聚集）。"""
    result: JsonObject = {}
    for m in re.finditer(r'"([^"\\]+)"\s*:\s*("(?:[^"\\]|\\.)*"|true|false|null|-?\d+(?:\.\d+)?)', text):
        # 正则逐段抓取 key-value：能解析的转成 JSON 值，否则保留原始串
        key, raw = m.group(1), m.group(2)
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result
