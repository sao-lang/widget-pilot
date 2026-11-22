"""ADR-010 §3 前置危险调用检测（确定性、零模型）。

三层中的前两层在此实现：①词表层（危险动作短语 + 契约内设备 API 白名单外的
接口名）；②AST/形状层（动态构造 API 名 `device[name]()`、字符串拼接调用）。
第三层便宜模型兜底是**可选建议**，不得单独放行——故不在此模块实现（无端点时
生产 fail-fast，且不作为门禁依据）。

危险动作词表：`patterns/dangerous_actions.json`，可由
`WIDGETPILOT_DANGEROUS_LEXICON_PATH` 按类别整体覆盖（外置非法 fail-fast），
与 ADR-005 敏感词库同纪律。危险接口名来自 `packages/contracts/device-apis.json`
单一事实来源，不在代码内置具体接口名。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from services.orchestrator.device_sim.detector import load_limits

__all__ = ["DangerVerdict", "detect_dangerous_calls"]

_LEXICON = Path(__file__).parent / "patterns" / "dangerous_actions.json"
_ENV_LEXICON = "WIDGETPILOT_DANGEROUS_LEXICON_PATH"

# 动态构造 API 名形状：device[name] / window.widgetDevice[name] / ["api"]()
_DYNAMIC_API = re.compile(
    r"(?:widgetDevice|device)\s*\[\s*[^\]\d'\"]|"
    r"(?:widgetDevice|device)\s*\[\s*['\"]?[a-zA-Z_]\w*['\"]?\s*\]\s*\("
)
_DANGEROUS_GLOBALS = re.compile(
    r"\b(eval|Function|fetch|XMLHttpRequest|child_process|require\s*\(\s*['\"]fs|process\.env)\s*\("
)


@dataclass(frozen=True, slots=True)
class DangerVerdict:
    """危险调用判定（hit=True 时 violations 含类别标签）。"""

    hit: bool
    violations: tuple[str, ...] = field(default_factory=tuple)


def _load_lexicon() -> dict[str, list[str]]:
    """加载内置词表 + 可选外置覆盖；外置非法即抛（fail-closed）。"""
    raw = json.loads(_LEXICON.read_text(encoding="utf-8"))
    override = os.getenv(_ENV_LEXICON, "")
    if override:
        ext = json.loads(Path(override).read_text(encoding="utf-8"))
        if not isinstance(ext, dict):
            raise ValueError("dangerous lexicon override must be object")
        for key in ("dangerous_action_phrases", "revocation_phrases"):
            if key in ext:
                if not isinstance(ext[key], list) or not all(isinstance(x, str) for x in ext[key]):
                    raise ValueError(f"dangerous lexicon.{key} must be string array")
                raw[key] = ext[key]
    return raw


def _interface_names() -> list[str]:
    """契约内所有设备 API 名（合法接口，命中不算危险）。"""
    return list(load_limits().allowed_apis)


def detect_dangerous_calls(
    text: str,
    *,
    extracted_texts: tuple[str, ...] = (),
    source_kind: str = "description",
) -> DangerVerdict:
    """检测危险接口/高危 API/危险动作；命中任一即 hit=True。

    Args:
        text: 用户描述或模型输出源码（source_kind 区分）。
        extracted_texts: 附件/检索提取文本（按不可信数据同样扫描）。
        source_kind: "description" | "code"，决定 AST 形状层是否启用。
    """
    corpus = "\n".join([text, *extracted_texts])
    lex = _load_lexicon()
    violations: list[str] = []

    # ① 危险动作词表
    for phrase in lex["dangerous_action_phrases"]:
        if phrase and phrase in corpus:
            violations.append("dangerous_action:" + phrase)

    # ② 设备 API 名白名单外的调用（契约单一事实来源）
    allowed = set(_interface_names())
    for match in re.finditer(r"(?:window\.)?widgetDevice(?:\.device)?\.([a-zA-Z_]\w*)", corpus):
        api_name = match.group(1)
        if f"device.{api_name}" not in allowed and api_name not in allowed:
            violations.append("dangerous_api:device." + api_name)

    # ③ AST/形状层（代码场景）：动态构造 API + 危险全局调用
    if source_kind == "code":
        if _DYNAMIC_API.search(corpus):
            violations.append("dynamic_api:constructed-name")
        if _DANGEROUS_GLOBALS.search(corpus):
            violations.append("dangerous_action:global")

    return DangerVerdict(hit=bool(violations), violations=tuple(violations))
