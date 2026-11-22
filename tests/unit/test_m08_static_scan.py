"""M08 静态扫描单测：AST 确定性判定，不信全文正则。"""

from __future__ import annotations

from services.guardrails.static_scan import (
    collect_capability_calls,
    collect_imports,
    parse_tsx,
    scan_forbidden,
)


def test_comments_and_string_literals_are_not_semantic_hits() -> None:
    """注释与字符串里的 fetch/eval 不是调用，不得命中。"""
    source = """// fetch("https://evil.example")
/* eval("1") */
const label = "eval(1) fetch(x)";
export default function Widget() { return <div>{label}</div>; }
"""
    assert scan_forbidden(parse_tsx(source)) == []


def test_jsx_text_is_not_a_semantic_hit() -> None:
    """JSX 文本中的普通词不是标识符调用。"""
    source = "export default function Widget() { return <div>fetch eval</div>; }"
    assert scan_forbidden(parse_tsx(source)) == []


def test_unknown_import_leaves_position_and_hash_only() -> None:
    """未知 import 命中：位置 + sha256，无原文。"""
    source = 'import fs from "fs";\nexport default function Widget() { return null; }'
    violations = scan_forbidden(parse_tsx(source))
    hits = [v for v in violations if v.rule == "import_not_allowed"]
    assert len(hits) == 1
    assert hits[0].position == "1:0"
    assert hits[0].node_kind == "ImportDeclaration"
    assert len(hits[0].snippet_hash) == 64
    assert "fs" not in hits[0].snippet_hash


def test_dynamic_import_is_detected() -> None:
    """动态 import("fs") 也算未知 import，不能靠字面量绕过。"""
    source = "const m = await import(\"fs\");\nexport default function Widget() { return null; }"
    violations = scan_forbidden(parse_tsx(source))
    assert any(v.rule == "import_not_allowed" for v in violations)
    assert "fs" in collect_imports(parse_tsx(source))


def test_window_parent_and_process_members_are_blocked() -> None:
    """父页面 DOM / process 系统 API 逐条命中。"""
    source = """export default function Widget() {
  window.parent.postMessage("x", "*");
  process.env.SECRET;
  return null;
}
"""
    rules = {v.rule for v in scan_forbidden(parse_tsx(source))}
    assert "forbidden_member:window.parent" in rules
    assert "forbidden_member:process" in rules


def test_capability_calls_collected_from_bridge_binding() -> None:
    """bridge 调用点提取用于三方契约比对。"""
    source = """import React from "react";
import bridge from "@widget/bridge";
export default function Widget() {
  bridge.call("date");
  bridge.call("timer");
  return null;
}
"""
    assert collect_capability_calls(parse_tsx(source)) == frozenset({"date", "timer"})


def test_imports_collected_from_static_and_allowlisted() -> None:
    """静态 import 集合精确（不含成员属性名）。"""
    source = """import React from "react";
import { send } from "@widget/bridge";
export default function Widget() { return <div/>; }
"""
    assert collect_imports(parse_tsx(source)) == frozenset({"react", "@widget/bridge"})


def test_scan_forbidden_rejects_non_ast_input() -> None:
    """非 AST 输入 fail-closed，返回 ast_missing 命中而不是放行。"""
    violations = scan_forbidden("raw text")
    assert len(violations) == 1
    assert violations[0].rule == "ast_missing"
