"""M08 编译产物落证（R7）：容器内 esbuild 产出的 JS 写对象存储并返回带 hash 引用。

编译本身在一次性沙箱内执行（entry.py 负责）；本模块只做产物的确定性落证与回读，
不在编排进程里编译/执行生成代码（I5）。
"""

from __future__ import annotations

from dataclasses import dataclass

from services.sandbox_runner.ports import EvidenceStorePort

__all__ = ["CompiledArtifact", "compile_tsx"]


@dataclass(slots=True, frozen=True)
class CompiledArtifact:
    """编译产物引用与摘要。"""

    ref: str
    hash: str
    source: str


def compile_tsx(
    prefix: str,
    compiled_source: str,
    evidence_store: EvidenceStorePort,
) -> CompiledArtifact:
    """写 compiled.js 并返回 (ref, hash)；缺产物时抛 ValueError。"""
    if not compiled_source:
        raise ValueError("COMPILE_FAILED: empty compiled source")
    key = f"{prefix}/compiled.js"
    payload = compiled_source.encode("utf-8")
    ref = evidence_store.put(key, payload)
    return CompiledArtifact(ref=ref.key + "#sha256:" + ref.hash, hash=ref.hash, source=compiled_source)
