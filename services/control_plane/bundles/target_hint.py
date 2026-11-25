"""P2 #36：从修改描述解析定位提示（ordinal / by_host / by_content）。

纯函数、零依赖 LLM。只解析用户明确说出的定位词；无提示时返回 last（最近一版），
与 registry.resolve_target 的 method 值域对齐。
"""
from __future__ import annotations

import re

__all__ = ["parse_target_hint"]

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# N1（review Round2）：复合数字「十二」必须整段捕获，否则 len(raw)>1 分支永不触发。
_ORDINAL = re.compile(r"第\s*([0-9]+|十[一二三四五六七八九]|[一二两三四五六七八九十])\s*个?")
_HOSTS = ("主屏幕", "主屏", "桌面", "锁屏", "负一屏")


def parse_target_hint(description: str) -> dict[str, str | int]:
    """解析定位提示 → registry.resolve_target 的 method/参数 dict。

    - "第二个日历改成 4x4" → {"method": "ordinal", "ordinal": 2}
    - "桌面那个日历改成 4x4" → {"method": "by_host", "host": "桌面"}
    - 无明确提示 → {"method": "last"}（最近一版）
    """
    # NB1：用户也可能直接回 bundle_id（"b1"）或裸序号（"1"）——取尾部 token。
    tail = description.strip().split()[-1] if description.strip() else ""
    if re.fullmatch(r"[0-9]+", tail):
        return {"method": "ordinal", "ordinal": int(tail)}
    if re.fullmatch(r"[Bb][0-9A-Za-z_-]{1,64}", tail):
        return {"method": "by_content", "content": tail}
    # NB1（review Round2）：澄清回答被拼到描述尾部，必须取**最后一个** ordinal
    # 才能覆盖原始定位（"把第二个日历改成4x4 第一个" → 选第一个）。
    matches = list(_ORDINAL.finditer(description))
    m = matches[-1] if matches else None
    if m:
        raw = m.group(1)
        if raw.isdigit():
            ordinal = int(raw)
        elif raw.startswith("十"):
            # N1（review）：复合数字「十二」= 10 + 2，不能只取首字。
            ordinal = 10 + (_CN_DIGITS.get(raw[1], 0) if len(raw) > 1 else 0)
        else:
            ordinal = _CN_DIGITS.get(raw, 0)
        if ordinal > 0:
            return {"method": "ordinal", "ordinal": ordinal}
    for host in _HOSTS:
        if host in description:
            return {"method": "by_host", "host": host}
    return {"method": "last"}
