"""ADR-014 CSV 工具组（parse / schema / filter / sort / select / aggregate / stats）。

设计纪律（与 media_processors 一致）：
- 纯函数：只读原文字节 + 参数，返回 MediaResult；不碰存储、不产生副作用。
- 用 Python 标准库 csv 严格解析（支持引号/转义），绝不 eval、不执行单元格内容。
- 行数 / 列数 / 输出预览均有硬上限；同输入同输出（排序有稳定平局裁决）。
- 单元格值恒当数据，不当指令；不可信由回灌前缀 [工具结果·不可信数据] 标注。
- 类型推断/聚合用纯代码，不调模型、不引入 pandas（零新增依赖）。
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Sequence
from datetime import date
from typing import cast

from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.attachments.constants import (
    MAX_CSV_COLUMNS,
    MAX_CSV_PREVIEW_ROWS,
    MAX_TEXT_ROWS,
)
from services.control_plane.attachments.media_processors import MediaResult

_CANDIDATE_DELIMITERS: tuple[str, ...] = (",", ";", "\t", "|")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _decode(data: bytes) -> str:
    """严格 UTF-8 解码，去 BOM（不因 BOM 把首列名弄脏）。"""
    return data.decode("utf-8", errors="strict").lstrip("\ufeff")


def _sniff_delimiter(text: str) -> str:
    """确定性分隔符嗅探：采样前 20 行，取候选分隔符中每行出现次数众数最高者。

    不依赖 csv.Sniffer 的概率模型（同输入可能飘），保证回放字节一致。
    """
    sample = text.splitlines()[:20] or [""]
    best, best_score = ",", 0
    for delim in _CANDIDATE_DELIMITERS:
        counts = [line.count(delim) for line in sample if line.strip()]
        if not counts:
            continue
        # 众数高且行间一致（极差小）者优先；平局按候选顺序（确定性）
        mode = max(set(counts), key=lambda c: (counts.count(c), c))
        score = mode * 100 - (max(counts) - min(counts))
        if score > best_score:
            best, best_score = delim, score
    return best


def _load_table(
    data: bytes,
    delimiter: str = "auto",
    has_header: bool = True,
) -> tuple[list[str], list[list[str]], str, int, bool]:
    """解析为 (header, rows, delimiter_used, skipped_rows, truncated)。

    csv.reader 处理引号与转义；列数不一致的行跳过并计数，不静默吞掉。
    """
    text = _decode(data)
    if not text.strip():
        raise ValueError("INPUT_INVALID: empty CSV")
    used = _sniff_delimiter(text) if delimiter == "auto" else delimiter
    if used not in (*_CANDIDATE_DELIMITERS, " "):
        raise ValueError("INPUT_INVALID: unsupported delimiter")
    reader = csv.reader(io.StringIO(text), delimiter=used)
    parsed = list(reader)
    if not parsed:
        raise ValueError("INPUT_INVALID: no rows")
    width = len(parsed[0])
    if width > MAX_CSV_COLUMNS:
        raise ValueError("INPUT_INVALID: column cap exceeded")
    header = [cell.strip() for cell in parsed[0]] if has_header else [f"col_{i}" for i in range(width)]
    body = parsed[1:] if has_header else parsed
    skipped = 0
    rows: list[list[str]] = []
    for row in body:
        if len(row) != width:
            skipped += 1
            continue
        if len(rows) >= MAX_TEXT_ROWS:
            break
        rows.append([cell.strip() for cell in row])
    return header, rows, used, skipped, len(body) > MAX_TEXT_ROWS


def _preview(
    header: Sequence[str], rows: Sequence[Sequence[str]], limit: int
) -> tuple[list[JsonValue], bool]:
    """有界预览：行数上限 MAX_CSV_PREVIEW_ROWS，返回 (行列表, 是否截断)。"""
    cap = min(max(1, limit), MAX_CSV_PREVIEW_ROWS)
    truncated = len(rows) > cap
    out: list[JsonValue] = []
    for row in rows[:cap]:
        out.append(cast(JsonValue, {h: v for h, v in zip(header, row)}))
    return out, truncated


def _infer_type(values: Sequence[str]) -> str:
    """列类型推断（确定性，优先级 number > date > bool > text）。"""
    nonempty = [v for v in values if v != ""]
    if not nonempty:
        return "empty"
    if all(_FLOAT_RE.match(v) for v in nonempty):
        return "number" if any("." in v or "e" in v.lower() for v in nonempty) else "integer"
    if all(_is_date(v) for v in nonempty):
        return "date"
    if all(v.lower() in ("true", "false", "yes", "no", "0", "1") for v in nonempty):
        return "boolean"
    return "text"


def _is_date(value: str) -> bool:
    """ISO 日期/日期时间真值校验（不用正则猜测）。"""
    try:
        date.fromisoformat(value[:10])
        return len(value) == 10 or value[10:11] in ("T", " ")
    except (ValueError, IndexError):
        return False


def _col_index(header: Sequence[str], field: str) -> int:
    """列名 → 下标（找不到即业务失败，不猜）。"""
    try:
        return list(header).index(field)
    except ValueError as exc:
        raise ValueError(f"INPUT_INVALID: unknown column {field!r}") from exc


def _num(value: str) -> float | None:
    """严格数值转换；非数值返回 None（不抛，交给调用方按文本处理）。"""
    return float(value) if _FLOAT_RE.match(value) else None


def _cell_json(header: Sequence[str], row: Sequence[str]) -> JsonObject:
    return cast(JsonObject, {h: v for h, v in zip(header, row)})


# --- 七个工具 --------------------------------------------------------------


def csv_parse(data: bytes, delimiter: str = "auto", has_header: bool = True, limit: int = 50) -> MediaResult:
    """严格解析 CSV：返回表头/行数/跳行数/有界预览。"""
    header, rows, used, skipped, truncated_total = _load_table(data, delimiter, has_header)
    preview, truncated = _preview(header, rows, limit)
    return MediaResult(
        "CsvParseProcessor",
        cast(
            JsonObject,
            {
                "delimiter": used,
                "columns": list(header),
                "row_count": len(rows),
                "skipped_rows": skipped,
                "truncated_source": truncated_total,
                "rows": preview,
                "truncated_preview": truncated,
            },
        ),
    )


def csv_schema(data: bytes, delimiter: str = "auto", has_header: bool = True) -> MediaResult:
    """每列类型推断 + 空值数 + 唯一值数（不下载整表进 prompt，只回统计）。"""
    header, rows, used, skipped, _ = _load_table(data, delimiter, has_header)
    columns: list[JsonObject] = []
    for idx, name in enumerate(header):
        values = [row[idx] for row in rows]
        columns.append(
            cast(
                JsonObject,
                {
                    "name": name,
                    "type": _infer_type(values),
                    "null_count": sum(1 for v in values if v == ""),
                    "unique_count": len(set(values)),
                },
            )
        )
    return MediaResult(
        "CsvSchemaProcessor",
        cast(
            JsonObject,
            {"delimiter": used, "row_count": len(rows), "skipped_rows": skipped, "columns": columns},
        ),
    )


def csv_filter(
    data: bytes,
    field: str,
    op: str,
    value: str,
    delimiter: str = "auto",
    has_header: bool = True,
    limit: int = 50,
) -> MediaResult:
    """按条件筛行：eq/ne/gt/gte/lt/lte/contains/startswith。

    若两侧都能转数值则按数值比较，否则按字符串；恒不使用 eval。
    """
    allowed = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "startswith"}
    if op not in allowed:
        raise ValueError("INPUT_INVALID: unsupported filter op")
    header, rows, used, skipped, _ = _load_table(data, delimiter, has_header)
    idx = _col_index(header, field)
    target_num = _num(value)

    def matches(cell: str) -> bool:
        if op == "contains":
            return value in cell
        if op == "startswith":
            return cell.startswith(value)
        cell_num = _num(cell)
        if cell_num is not None and target_num is not None:
            if op == "eq":
                return cell_num == target_num
            if op == "ne":
                return cell_num != target_num
            if op == "gt":
                return cell_num > target_num
            if op == "gte":
                return cell_num >= target_num
            if op == "lt":
                return cell_num < target_num
            return cell_num <= target_num  # lte
        if op == "eq":
            return cell == value
        if op == "ne":
            return cell != value
        if op == "gt":
            return cell > value
        if op == "gte":
            return cell >= value
        if op == "lt":
            return cell < value
        return cell <= value  # lte

    kept = [row for row in rows if matches(row[idx])]
    preview, truncated = _preview(header, kept, limit)
    return MediaResult(
        "CsvFilterProcessor",
        cast(
            JsonObject,
            {
                "delimiter": used,
                "field": field,
                "op": op,
                "value": value,
                "matched_count": len(kept),
                "skipped_rows": skipped,
                "rows": preview,
                "truncated": truncated,
            },
        ),
    )


def csv_sort(
    data: bytes,
    field: str,
    descending: bool = False,
    delimiter: str = "auto",
    has_header: bool = True,
    limit: int = 50,
) -> MediaResult:
    """按列排序：数值列按数值，其余按字符串；稳定平局裁决保确定性。"""
    header, rows, used, skipped, _ = _load_table(data, delimiter, has_header)
    idx = _col_index(header, field)
    indexed = list(enumerate(rows))
    all_numeric = all(_num(row[idx]) is not None for row in rows if row[idx] != "")

    def key(item: tuple[int, list[str]]):
        original_pos, row = item
        cell = row[idx]
        primary = _num(cell) if all_numeric and cell != "" else cell
        return (primary == "" if isinstance(primary, str) else False, primary, original_pos)

    indexed.sort(key=key, reverse=descending)
    kept = [row for _, row in indexed]
    preview, truncated = _preview(header, kept, limit)
    return MediaResult(
        "CsvSortProcessor",
        cast(
            JsonObject,
            {
                "delimiter": used,
                "field": field,
                "descending": descending,
                "row_count": len(kept),
                "skipped_rows": skipped,
                "rows": preview,
                "truncated": truncated,
            },
        ),
    )


def csv_select(
    data: bytes,
    fields: tuple[str, ...],
    delimiter: str = "auto",
    has_header: bool = True,
    limit: int = 50,
) -> MediaResult:
    """选列投影：保留指定列及顺序，缺列即业务失败（不猜）。"""
    if not fields:
        raise ValueError("INPUT_INVALID: fields must not be empty")
    header, rows, used, skipped, _ = _load_table(data, delimiter, has_header)
    indices = [_col_index(header, f) for f in fields]
    projected = [[row[i] for i in indices] for row in rows]
    preview, truncated = _preview(fields, projected, limit)
    return MediaResult(
        "CsvSelectProcessor",
        cast(
            JsonObject,
            {
                "delimiter": used,
                "fields": list(fields),
                "row_count": len(projected),
                "skipped_rows": skipped,
                "rows": preview,
                "truncated": truncated,
            },
        ),
    )


def csv_aggregate(
    data: bytes,
    op: str,
    field: str = "",
    group_by: str = "",
    delimiter: str = "auto",
    has_header: bool = True,
) -> MediaResult:
    """分组聚合：op=count/sum/mean/min/max；group_by 为空则全表聚合。"""
    allowed = {"count", "sum", "mean", "min", "max"}
    if op not in allowed:
        raise ValueError("INPUT_INVALID: unsupported aggregate op")
    header, rows, used, skipped, _ = _load_table(data, delimiter, has_header)
    value_idx = _col_index(header, field) if op != "count" and field else -1
    if op != "count" and value_idx < 0:
        raise ValueError("INPUT_INVALID: aggregate field required")
    group_idx = _col_index(header, group_by) if group_by else -1

    groups: dict[str, list[list[str]]] = {}
    for row in rows:
        key = row[group_idx] if group_idx >= 0 else "__all__"
        groups.setdefault(key, []).append(row)

    results: list[JsonObject] = []
    for key, members in groups.items():
        item: JsonObject = {"group": key if group_idx >= 0 else None, "count": len(members)}
        if op != "count":
            numbers = [_num(row[value_idx]) for row in members]
            valid = [n for n in numbers if n is not None]
            item["non_numeric"] = len(members) - len(valid)
            if valid:
                if op == "sum":
                    item["value"] = sum(valid)
                elif op == "mean":
                    item["value"] = sum(valid) / len(valid)
                elif op == "min":
                    item["value"] = min(valid)
                else:
                    item["value"] = max(valid)
            else:
                item["value"] = None
        results.append(item)
    results.sort(key=lambda r: str(r.get("group")))  # 确定性组序
    return MediaResult(
        "CsvAggregateProcessor",
        cast(
            JsonObject,
            {
                "delimiter": used,
                "op": op,
                "field": field or None,
                "group_by": group_by or None,
                "group_count": len(results),
                "skipped_rows": skipped,
                "results": results,
            },
        ),
    )


def csv_stats(data: bytes, delimiter: str = "auto", has_header: bool = True) -> MediaResult:
    """数值列的描述统计（count/min/max/mean/sum），非数值列跳过不编造。"""
    header, rows, used, skipped, _ = _load_table(data, delimiter, has_header)
    columns: list[JsonObject] = []
    for idx, name in enumerate(header):
        values = [_num(row[idx]) for row in rows]
        valid = [v for v in values if v is not None]
        if not valid:
            continue
        columns.append(
            cast(
                JsonObject,
                {
                    "name": name,
                    "count": len(valid),
                    "min": min(valid),
                    "max": max(valid),
                    "mean": sum(valid) / len(valid),
                    "sum": math.fsum(valid),
                },
            )
        )
    return MediaResult(
        "CsvStatsProcessor",
        cast(
            JsonObject,
            {"delimiter": used, "row_count": len(rows), "skipped_rows": skipped, "numeric_columns": columns},
        ),
    )
