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
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
            out.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
            out.append(ch)
        else:
            out.append(ch)
    if in_string:
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
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth == 0 and not in_string


def _best_effort(text: str) -> JsonObject:
    """尽力从非法文本中提取 key-value 对（结构不确定，用 JsonObject 聚集）。"""
    result: JsonObject = {}
    for m in re.finditer(r'"([^"\\]+)"\s*:\s*("(?:[^"\\]|\\.)*"|true|false|null|-?\d+(?:\.\d+)?)', text):
        key, raw = m.group(1), m.group(2)
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result
