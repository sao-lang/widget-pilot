"""ADR-014 CSV 工具组：注册 + 纯函数行为 + 端到端 handler。"""
from __future__ import annotations

import hashlib
import json
from typing import cast

import pytest

from packages.agent_core.tools.invoke import load
from packages.agent_core.tools.registry import (
    REGISTRY,
    capability_detail,
    level0,
    unfreeze_registry,
)
from packages.agent_core.types import Decision, DecisionKind
from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.attachments.constants import (
    MAX_CSV_PREVIEW_ROWS,
    MAX_TEXT_ROWS,
)
from services.control_plane.attachments.csv_processors import (
    csv_aggregate,
    csv_filter,
    csv_parse,
    csv_schema,
    csv_select,
    csv_sort,
    csv_stats,
)
from services.orchestrator.capabilities import register_capabilities

_SAMPLE = b"name,age,city\nAlice,30,Beijing\nBob,25,Shanghai\nCarol,41,Beijing\n"


def _obj(value: JsonValue) -> JsonObject:
    """测试辅助：把 JsonValue 收窄为 JsonObject（断言前置条件）。"""
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def _list(value: JsonValue) -> list[JsonValue]:
    """测试辅助：把 JsonValue 收窄为列表（断言前置条件）。"""
    assert isinstance(value, list)
    return value

_CSV_IDS = {
    "tool.csv.parse",
    "tool.csv.schema",
    "tool.csv.filter",
    "tool.csv.sort",
    "tool.csv.select",
    "tool.csv.aggregate",
    "tool.csv.stats",
}


class MemoryWriter:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def put_derived(self, source_key: str, name: str, data: bytes) -> str:
        key = f"derived/{source_key}/{name}"
        self.items[key] = data
        return f"{key}#sha256:{hashlib.sha256(data).hexdigest()}"


def teardown_function() -> None:
    unfreeze_registry()
    REGISTRY.clear()


def test_csv_tools_registered_and_l1_schema_real() -> None:
    unfreeze_registry()
    REGISTRY.clear()
    reader = lambda ref: _SAMPLE
    register_capabilities(gateway=None, blob_reader=reader, derived_writer=MemoryWriter())
    ids = {str(item["id"]) for item in level0()}
    assert _CSV_IDS <= ids
    assert len(ids) == 34  # 6 首批 + 14 媒体 + 7 CSV + 6 视觉 + 3 规程（加 weather）
    for item in level0():
        assert set(item) == {"id", "kind", "brief", "cost_hint"}
    detail = capability_detail("tool.csv.filter")
    schema = json.dumps(detail["args_schema"])
    assert "field" in schema and "op" in schema


def test_csv_parse_strict_and_quoted() -> None:
    quoted = b'name,note\nAlice,"hello, world"\nBob,"line1\nline2"\n'
    result = csv_parse(quoted)
    assert result.extracted["columns"] == ["name", "note"]
    assert result.extracted["row_count"] == 2
    rows = result.extracted["rows"]
    assert isinstance(rows, list)
    assert rows[0]["note"] == "hello, world"  # type: ignore[index]
    assert "\n" in rows[1]["note"]  # type: ignore[index]


def test_csv_parse_sniffs_semicolon_and_skips_malformed() -> None:
    data = b"a;b\n1;2\n3\n4;5\n"
    result = csv_parse(data)
    assert result.extracted["delimiter"] == ";"
    assert result.extracted["row_count"] == 2
    assert result.extracted["skipped_rows"] == 1


def test_csv_schema_infers_types() -> None:
    result = csv_schema(_SAMPLE)
    cols = result.extracted["columns"]
    assert isinstance(cols, list)
    by_name = {c["name"]: c for c in cols if isinstance(c, dict)}
    assert by_name["name"]["type"] == "text"
    assert by_name["age"]["type"] == "integer"
    assert by_name["city"]["unique_count"] == 2


def test_csv_filter_numeric_and_string_ops() -> None:
    numeric = csv_filter(_SAMPLE, "age", "gte", "30")
    assert numeric.extracted["matched_count"] == 2
    text = csv_filter(_SAMPLE, "city", "eq", "Beijing")
    assert text.extracted["matched_count"] == 2
    contains = csv_filter(_SAMPLE, "name", "contains", "li")
    assert contains.extracted["matched_count"] == 1  # 仅 Alice 含 "li"
    with pytest.raises(ValueError, match="unsupported filter op"):
        csv_filter(_SAMPLE, "age", "regex", "3")


def test_csv_sort_numeric_vs_string() -> None:
    asc = csv_sort(_SAMPLE, "age")
    rows = _list(asc.extracted["rows"])
    assert [_obj(r)["age"] for r in rows] == ["25", "30", "41"]
    desc = csv_sort(_SAMPLE, "name", descending=True)
    assert [_obj(r)["name"] for r in _list(desc.extracted["rows"])] == ["Carol", "Bob", "Alice"]


def test_csv_select_and_aggregate() -> None:
    projected = csv_select(_SAMPLE, ("city", "name"))
    assert projected.extracted["fields"] == ["city", "name"]
    rows = _list(projected.extracted["rows"])
    assert list(_obj(rows[0]).keys()) == ["city", "name"]

    agg = csv_aggregate(_SAMPLE, op="mean", field="age", group_by="city")
    assert agg.extracted["group_count"] == 2
    results = _list(agg.extracted["results"])
    beijing = next(r for r in results if _obj(r)["group"] == "Beijing")
    assert _obj(beijing)["value"] == 35.5  # (30+41)/2

    total = csv_aggregate(_SAMPLE, op="count")
    assert total.extracted["group_count"] == 1
    assert _obj(_list(total.extracted["results"])[0])["count"] == 3


def test_csv_stats_only_numeric_columns() -> None:
    result = csv_stats(_SAMPLE)
    cols = _list(result.extracted["numeric_columns"])
    assert len(cols) == 1
    assert _obj(cols[0])["name"] == "age"
    assert _obj(cols[0])["min"] == 25 and _obj(cols[0])["max"] == 41


def test_csv_no_eval_and_bounded_preview() -> None:
    hostile = b"expr\n__import__('os').system('echo pwned')\n"
    result = csv_parse(hostile)
    rows = _list(result.extracted["rows"])
    assert "__import__" in str(_obj(rows[0])["expr"])  # 值仅作字符串，不执行

    many = b"n\n" + b"".join(f"{i}\n".encode() for i in range(MAX_TEXT_ROWS + 10))
    capped = csv_parse(many)
    assert capped.extracted["row_count"] == MAX_TEXT_ROWS
    assert capped.extracted["truncated_source"] is True
    assert len(_list(capped.extracted["rows"])) <= MAX_CSV_PREVIEW_ROWS


def test_csv_handler_writes_no_derived_and_emits_json() -> None:
    """CSV 工具输出走 JSON 回灌，不写派生字节。"""
    unfreeze_registry()
    REGISTRY.clear()
    writer = MemoryWriter()
    register_capabilities(
        gateway=None,
        blob_reader=lambda ref: _SAMPLE,
        derived_writer=writer,
    )
    ref = f"att/u/s/a/original#sha256:{hashlib.sha256(_SAMPLE).hexdigest()}"
    result = load(
        Decision(
            DecisionKind.LOAD_CAPABILITY,
            capability_id="tool.csv.parse",
            level=2,
            args={"source_ref": ref, "limit": 2},
        )
    )
    payload = json.loads(result.output)
    assert payload["handler"] == "CsvParseProcessor"
    assert payload["extracted"]["row_count"] == 3
    assert len(payload["extracted"]["rows"]) == 2
    assert writer.items == {}  # 结构化结果不写派生对象
