"""本地模型权重加载与诚信校验（podman 只读卷挂载）。

权重不进 git：infra/models/slot-tagger/<version>/{model.onnx,labels.json,manifest.json}
缺文件 / sha256 不符 / manifest 缺字段 → fail-fast（ValueError），不静默降级。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.guardrails.interpreter.types import WeightsManifest

_MODEL_FILE = "model.onnx"
_MANIFEST_FILE = "manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_weights(path: str, *, expect_sha256: str | None = None) -> WeightsManifest:
    """校验并返回 manifest；任何不一致都抛 ValueError（fail-closed）。"""
    root = Path(path)
    model = root / _MODEL_FILE
    manifest_file = root / _MANIFEST_FILE
    if not model.is_file() or not manifest_file.is_file():
        raise ValueError(f"本地模型权重缺失：{path}（需 {_MODEL_FILE} + {_MANIFEST_FILE}）")

    raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(k in raw for k in ("version", "sha256", "sample_count")):
        raise ValueError(f"manifest 字段不全：{manifest_file}")
    manifest = WeightsManifest(
        version=str(raw["version"]),
        sha256=str(raw["sha256"]),
        sample_count=int(raw["sample_count"]),
        intent_families=tuple(raw.get("intent_families", ())),
        license=str(raw.get("license", "")),
    )
    actual = _sha256(model.read_bytes())
    expected = expect_sha256 or manifest.sha256
    if actual != expected:
        raise ValueError(f"权重 sha256 不符：manifest={expected} actual={actual}")
    return manifest


__all__ = ["load_weights"]
