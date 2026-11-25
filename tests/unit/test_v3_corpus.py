"""样本库契约测试（ADR-017）：封闭类型学 + 覆盖度。

防止「每次冒新说法就补正则」：新说法只能作为新样本落进封闭维度组合，
若必须新增维度值，说明成分体系有漏，测试会 FAIL 强制走 ADR 变更。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CORPUS = Path(__file__).parents[2] / "docs" / "samples" / "widget-description-corpus.json"


def _load() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_corpus_exists_and_shape() -> None:
    data = _load()
    assert data["version"]
    assert len(data["samples"]) >= 60
    for sample in data["samples"]:
        assert set(sample) >= {"id", "scene", "text", "components"}
        assert sample["text"].strip()


def test_all_dimension_values_are_closed() -> None:
    """每条样本的九维取值必须在 taxonomy 封闭集合内。"""
    data = _load()
    taxonomy = data["taxonomy"]
    for sample in data["samples"]:
        for dim, value in sample["components"].items():
            if dim not in taxonomy:
                continue  # instances/attachments/constraint/note 是扩展字段
            allowed = taxonomy[dim]
            values = str(value).split("+")
            for v in values:
                assert v in allowed, f"{sample['id']}: {dim}={v!r} 不在封闭集合 {allowed}"


def test_coverage_of_scenarios() -> None:
    """关键场景必须有足够样本：修改、附件使用、多 widget。"""
    data = _load()
    intents = Counter(s["components"]["intent"] for s in data["samples"])
    assert intents["create"] >= 30
    assert intents["modify"] >= 14
    attach_roles = Counter(s["components"]["attach_role"] for s in data["samples"])
    assert sum(v for k, v in attach_roles.items() if k != "none") >= 20
    operations = {s["components"]["operation"] for s in data["samples"]}
    assert {"set", "add", "remove", "replace", "style"} <= operations
    targets = {s["components"]["target"] for s in data["samples"]}
    assert {"last", "ordinal", "by_host", "by_content", "all"} <= targets


def test_multi_step_and_multi_attachment_samples_exist() -> None:
    """复杂样本：多实例、多附件、工具链约束都要有。"""
    data = _load()
    assert any(s["components"].get("instances", 0) >= 2 for s in data["samples"])
    assert any(s["components"].get("attachments", 0) >= 2 for s in data["samples"])
    constraints = [s["components"].get("constraint", "") for s in data["samples"]]
    assert any("frame_at" in c for c in constraints)
    assert any("crop" in c for c in constraints)
    assert any("filter" in c or "date" in c for c in constraints)
