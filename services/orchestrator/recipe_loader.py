"""M07 规程正文加载（ADR-003 / FR-9）。

规程正文存 `packages/prompts/recipes/<recipe_id>.md`，frontmatter 声明
recipe_id/version/token_cost。本模块只读索引与加载，不编写规程（M10 只索引口径）。
recipe_id 经白名单字符校验，杜绝路径穿越。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_RECIPE_ID_RE = re.compile(r"^[a-z][a-z0-9.]*$")
"""recipe_id 合法字符：小写字母/数字/点；拒绝斜杠与上级路径。"""


def _recipes_dir() -> Path:
    """规程库根目录（相对项目根，测试与生产同路径）。"""
    return Path(__file__).resolve().parents[2] / "packages" / "prompts" / "recipes"


@dataclass(slots=True, frozen=True)
class RecipeMeta:
    """规程元数据（frontmatter 解析结果）。

    Attributes:
        recipe_id: 规程唯一 id。
        version: 版本号（如 v3）。
        token_cost: 预估加载 token。
        text: 规程正文（frontmatter 之后的 Markdown）。
    """

    recipe_id: str
    version: str
    token_cost: int
    text: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """解析 `---` 包裹的 frontmatter，返回 (元数据字典, 正文)。"""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, raw
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1 :]).strip()


def load_recipe(recipe_id: str) -> RecipeMeta:
    """按 recipe_id 加载规程正文与元数据。

    Args:
        recipe_id: 规程句柄（合法字符白名单）。

    Returns:
        RecipeMeta。

    Raises:
        ValueError: recipe_id 非法（路径穿越防护）。
        FileNotFoundError: 规程文件不存在（fail-closed，不返回空正文）。
    """
    if not _RECIPE_ID_RE.fullmatch(recipe_id):
        raise ValueError(f"invalid recipe_id: {recipe_id!r}")
    path = _recipes_dir() / f"{recipe_id}.md"
    if not path.is_file():
        raise FileNotFoundError(f"recipe not found: {recipe_id}")
    meta, text = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return RecipeMeta(
        recipe_id=meta.get("recipe_id", recipe_id),
        version=meta.get("version", "v0"),
        token_cost=int(meta.get("token_cost", "0")),
        text=text,
    )


def scan_recipes() -> tuple[RecipeMeta, ...]:
    """扫描规程库全部 .md 文件，按 recipe_id 排序（确定性）。"""
    directory = _recipes_dir()
    if not directory.is_dir():
        return ()
    result: list[RecipeMeta] = []
    for path in sorted(directory.glob("*.md")):
        result.append(load_recipe(path.stem))
    return tuple(result)
