"""M08 AST 静态扫描（R3/R5）：禁止内容、import 白名单、bridge 能力调用点提取。

确定性说明：TSX 无 Python 官方 parser，这里实现的是**结构化 token + 位置扫描**——
先按字符状态机切出（跳过注释、字符串字面量、JSX 文本）token 流，再在 token 序列上
识别 ImportDeclaration / CallExpression / MemberExpression / ExportDefaultDeclaration。
判定依赖 token 类型与相邻位置，不是 ``re.search`` 全文匹配；字符串与注释内的
``fetch``/``eval`` 不会被当调用，JSX 文本也不会误命中。片段只留 sha256 与节点类别，
不写敏感词原文（ADR-005）。
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.enums import IMPORT_ALLOWLIST, Capability
from services.guardrails.output_ports import Violation, sha256_hex

__all__ = [
    "CallExpr",
    "ImportStmt",
    "TsxAst",
    "collect_capability_calls",
    "collect_imports",
    "has_default_widget",
    "parse_tsx",
    "scan_forbidden",
]

_FORBIDDEN_CALLEES: frozenset[str] = frozenset(
    {"eval", "fetch", "XMLHttpRequest", "WebSocket", "Function", "require", "importScripts"}
)
_FORBIDDEN_MEMBER_ROOTS: frozenset[str] = frozenset({"window.parent", "window.top", "document.cookie", "process", "globalThis"})
_CAPABILITY_VALUES: frozenset[str] = frozenset(cap.value for cap in Capability)
_IDENT_START = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$")
_IDENT_PART = _IDENT_START | frozenset("0123456789")


@dataclass(slots=True, frozen=True)
class Token:
    """带位置的最小词法单元。"""

    kind: str
    value: str
    line: int
    col: int


@dataclass(slots=True, frozen=True)
class ImportStmt:
    """静态/动态 import 语句。"""

    module: str
    position: str
    bindings: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class CallExpr:
    """调用表达式，callee 为 token 路径（如 bridge.call）。"""

    callee: str
    first_arg: str | None
    position: str


@dataclass(slots=True, frozen=True)
class TsxAst:
    """轻量 TSX 结构：token 流 + 四类结构化节点。"""

    source: str
    tokens: tuple[Token, ...]
    imports: tuple[ImportStmt, ...]
    calls: tuple[CallExpr, ...]
    members: tuple[str, ...]
    default_export: str | None
    bridge_bindings: tuple[str, ...]


def parse_tsx(source: str) -> TsxAst:
    """把 TSX 源码解析为可断言的结构；不执行、不求值。"""
    tokens = _tokenize(source)
    imports = _extract_imports(tokens)
    calls = _extract_calls(tokens)
    members = _extract_members(tokens)
    return TsxAst(
        source=source,
        tokens=tokens,
        imports=imports,
        calls=calls,
        members=members,
        default_export=_extract_default_export(tokens),
        bridge_bindings=tuple(
            binding
            for stmt in imports
            if stmt.module == "@widget/bridge"
            for binding in stmt.bindings
        ),
    )


def has_default_widget(ast_root: object) -> bool:
    """默认导出名必须为契约约定的 Widget。"""
    return isinstance(ast_root, TsxAst) and ast_root.default_export == "Widget"


def collect_imports(ast_root: object) -> frozenset[str]:
    """实际 import 模块集合（含动态 import）。"""
    if not isinstance(ast_root, TsxAst):
        return frozenset()
    return frozenset(stmt.module for stmt in ast_root.imports)


def collect_capability_calls(ast_root: object) -> frozenset[str]:
    """bridge 调用点上的 capability 字符串集合（三方比对用）。"""
    if not isinstance(ast_root, TsxAst):
        return frozenset()
    found: set[str] = set()
    for call in ast_root.calls:
        root = call.callee.split(".", 1)[0]
        if root in ast_root.bridge_bindings and call.first_arg in _CAPABILITY_VALUES:
            found.add(call.first_arg)
    return frozenset(found)


def scan_forbidden(ast_root: object) -> list[Violation]:
    """扫 eval/外联/未知 import/系统 API；非 AST 输入 fail-closed。"""
    if not isinstance(ast_root, TsxAst):
        return [
            Violation(
                node_kind="AST",
                position="0:0",
                snippet_hash=sha256_hex(b"ast_missing"),
                rule="ast_missing",
            )
        ]

    violations: list[Violation] = []
    for stmt in ast_root.imports:
        if stmt.module not in IMPORT_ALLOWLIST:
            violations.append(
                Violation(
                    node_kind="ImportDeclaration",
                    position=stmt.position,
                    snippet_hash=sha256_hex(stmt.module.encode()),
                    rule="import_not_allowed",
                )
            )
    for call in ast_root.calls:
        if call.callee in _FORBIDDEN_CALLEES:
            violations.append(
                Violation(
                    node_kind="CallExpression",
                    position=call.position,
                    snippet_hash=sha256_hex(call.callee.encode()),
                    rule=f"forbidden_call:{call.callee}",
                )
            )
        elif call.callee == "import" and call.first_arg not in IMPORT_ALLOWLIST:
            module = call.first_arg or ""
            violations.append(
                Violation(
                    node_kind="ImportExpression",
                    position=call.position,
                    snippet_hash=sha256_hex(module.encode()),
                    rule="import_not_allowed",
                )
            )
    for member in ast_root.members:
        root = member.split(".", 1)[0]
        if member in _FORBIDDEN_MEMBER_ROOTS or root in _FORBIDDEN_MEMBER_ROOTS:
            violations.append(
                Violation(
                    node_kind="MemberExpression",
                    position="0:0",
                    snippet_hash=sha256_hex(member.encode()),
                    rule=f"forbidden_member:{member if member in _FORBIDDEN_MEMBER_ROOTS else root}",
                )
            )
    return violations


def _tokenize(source: str) -> tuple[Token, ...]:
    """字符状态机：跳过注释/字符串内容/JSX 文本，保留结构化 token 与位置。"""
    tokens: list[Token] = []
    index = 0
    line = 1
    col = 0
    jsx_text = False
    in_tag = False
    closing_tag = False

    def advance(chars: str) -> None:
        nonlocal line, col
        for char in chars:
            if char == "\n":
                line += 1
                col = 1
            else:
                col += 1

    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if char == "/" and nxt == "/":
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            advance(source[index:end])
            index = end
            continue
        if char == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            end = len(source) if end < 0 else end + 2
            advance(source[index:end])
            index = end
            continue
        if char in ("'", '"', "`"):
            start_line, start_col, start = line, col, index
            index += 1
            advance(char)
            while index < len(source):
                current = source[index]
                advance(current)
                index += 1
                if current == "\\" and index < len(source):
                    advance(source[index])
                    index += 1
                    continue
                if current == char:
                    break
            tokens.append(
                Token("string", source[start + 1 : index - 1], start_line, start_col)
            )
            continue
        if jsx_text:
            if char == "<" and nxt and (nxt.isalpha() or nxt == "/"):
                jsx_text = False
                continue
            if char == "{":
                jsx_text = False
                tokens.append(Token("punct", char, line, col))
                advance(char)
                index += 1
                continue
            start_line, start_col, start = line, col, index
            while index < len(source) and source[index] not in "<{\n;}":
                advance(source[index])
                index += 1
            tokens.append(Token("jsxtext", source[start:index], start_line, start_col))
            if index < len(source) and source[index] in "\n;}":
                jsx_text = False
            continue
        if char == "<" and nxt and (nxt.isalpha() or nxt == "/"):
            tokens.append(Token("punct", char, line, col))
            advance(char)
            index += 1
            in_tag = True
            closing_tag = nxt == "/"
            continue
        if char == ">":
            tokens.append(Token("punct", char, line, col))
            advance(char)
            index += 1
            if in_tag:
                in_tag = False
                jsx_text = not closing_tag
                closing_tag = False
            continue
        if char in _IDENT_START:
            start_line, start_col, start = line, col, index
            while index < len(source) and source[index] in _IDENT_PART:
                advance(source[index])
                index += 1
            tokens.append(Token("ident", source[start:index], start_line, start_col))
            continue
        if char.isdigit():
            start_line, start_col, start = line, col, index
            while index < len(source) and (source[index].isdigit() or source[index] == "."):
                advance(source[index])
                index += 1
            tokens.append(Token("number", source[start:index], start_line, start_col))
            continue
        if char.isspace():
            advance(char)
            index += 1
            continue
        tokens.append(Token("punct", char, line, col))
        advance(char)
        index += 1
    return tuple(tokens)


def _extract_imports(tokens: tuple[Token, ...]) -> tuple[ImportStmt, ...]:
    findings: list[ImportStmt] = []
    for pos, token in enumerate(tokens):
        if token.kind != "ident" or token.value != "import":
            continue
        following = tokens[pos + 1] if pos + 1 < len(tokens) else None
        if following is not None and following.value == "(":
            module = tokens[pos + 2].value if pos + 2 < len(tokens) and tokens[pos + 2].kind == "string" else ""
            findings.append(ImportStmt(module, f"{token.line}:{token.col}", ()))
            continue
        module = ""
        bindings: list[str] = []
        cursor = pos + 1
        while cursor < len(tokens):
            item = tokens[cursor]
            if module and item.value in {";", "from"}:
                break
            if item.kind == "string" and not module:
                module = item.value
            elif item.kind == "ident" and item.value not in {"from", "as"}:
                bindings.append(item.value)
            cursor += 1
        if module:
            findings.append(ImportStmt(module, f"{token.line}:{token.col}", tuple(bindings)))
    return tuple(findings)


def _extract_calls(tokens: tuple[Token, ...]) -> tuple[CallExpr, ...]:
    findings: list[CallExpr] = []
    for pos, token in enumerate(tokens):
        if token.kind != "ident" or pos + 1 >= len(tokens):
            continue
        if tokens[pos + 1].value != "(" or tokens[pos + 1].kind != "punct":
            continue
        callee = token.value
        if pos >= 2 and tokens[pos - 1].value == "." and tokens[pos - 2].kind == "ident":
            callee = f"{tokens[pos - 2].value}.{token.value}"
        first_arg = tokens[pos + 2].value if pos + 2 < len(tokens) and tokens[pos + 2].kind == "string" else None
        findings.append(CallExpr(callee, first_arg, f"{token.line}:{token.col}"))
    return tuple(findings)


def _extract_members(tokens: tuple[Token, ...]) -> tuple[str, ...]:
    findings: list[str] = []
    for pos in range(len(tokens) - 2):
        if (
            tokens[pos].kind == "ident"
            and tokens[pos + 1].value == "."
            and tokens[pos + 2].kind == "ident"
        ):
            findings.append(f"{tokens[pos].value}.{tokens[pos + 2].value}")
    return tuple(findings)


def _extract_default_export(tokens: tuple[Token, ...]) -> str | None:
    for pos in range(len(tokens) - 2):
        if tokens[pos].value != "export" or tokens[pos + 1].value != "default":
            continue
        candidate = tokens[pos + 2]
        if candidate.kind != "ident":
            return None
        if candidate.value in {"function", "class"} and pos + 3 < len(tokens):
            name = tokens[pos + 3]
            return name.value if name.kind == "ident" else None
        return candidate.value
    return None
