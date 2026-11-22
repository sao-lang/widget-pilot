"""M08 真像素快照测试（R8/R13）：snapshot.png 由 headless Chromium 渲染并带 hash。

RED → GREEN：证据包同时写 snapshot.html（结构）与 snapshot.png（像素），
verify 覆盖 png；沙箱报告缺 png 时不得伪造（snapshot_png_ref=None）。
模型端口全无（本用例不涉模型），渲染在沙箱内真浏览器完成。
"""
from __future__ import annotations

import base64

from services.guardrails.output_ports import EvidenceRefs
from services.sandbox_runner.evidence import verify_evidence_refs, write_evidence
from services.sandbox_runner.ports import CommandResult
from tests.support.m08 import MemoryEvidenceStore

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-fake-but-realistic-png-bytes").decode("ascii")


def test_evidence_writes_snapshot_png_with_hash() -> None:
    """A17/R13：png 写入证据包，引用带 sha256，回读校验通过。"""
    store = MemoryEvidenceStore()
    compiled = store.put("code", b"compiled-js")
    compiled_ref = f"code#sha256:{compiled.hash}"
    refs = write_evidence(
        "evidence/s1/r1",
        {"stages": {}},
        b"log",
        b"<div/>",
        [("size_assert", True)],
        compiled_ref,
        "abc",
        store,
        base64.b64decode(_PNG),
    )
    assert refs.snapshot_png_ref
    assert "#sha256:" in refs.snapshot_png_ref
    assert refs.snapshot_png_ref.startswith("evidence/s1/r1/snapshot.png")
    assert verify_evidence_refs(store, refs) is True
    assert store.get(refs.snapshot_png_ref) == base64.b64decode(_PNG)


def test_evidence_png_optional_backward_compatible() -> None:
    """无 png（降级/失败路径）时 png_ref=None，其余证据仍完整。"""
    store = MemoryEvidenceStore()
    compiled = store.put("code", b"compiled-js")
    refs = write_evidence(
        "evidence/s1/r2", {"stages": {}}, b"log", b"<div/>",
        [("size_assert", True)], f"code#sha256:{compiled.hash}", compiled.hash, store,
    )
    assert refs.snapshot_png_ref is None
    # 降级路径无像素证据：verify 仍可判 True（不覆盖 None 项），
    # 但 sandbox 是否 passed 由 reason_code 决定，不由 png 是否缺失决定。
    assert store.get(refs.log_ref) == b"log"


def test_verify_rejects_tampered_png() -> None:
    """A17：png 被篡改则 verify False（不得判 PASSED）。"""
    store = MemoryEvidenceStore()
    compiled = store.put("code", b"compiled-js")
    refs = write_evidence(
        "evidence/s1/r3", {"stages": {}}, b"log", b"<div/>",
        [("size_assert", True)], f"code#sha256:{compiled.hash}", compiled.hash, store,
        base64.b64decode(_PNG),
    )
    assert refs.snapshot_png_ref
    store.tamper(refs.snapshot_png_ref.split("#")[0], b"tampered-png")
    assert verify_evidence_refs(store, refs) is False


def test_evidence_refs_default_png_none() -> None:
    """老构造（6 位）仍可用，png_ref 默认 None——降级路径不破坏。"""
    refs = EvidenceRefs("log", "report", None, (), "code", "hash")
    assert refs.snapshot_png_ref is None


def test_run_sandbox_wires_png_from_renderer() -> None:
    """R8：run_sandbox 把 html 交 renderer 渲染 png 并落证据。"""
    import hashlib

    from services.sandbox_runner.entry import run_sandbox
    from services.sandbox_runner.snapshot import SnapshotRendererPort
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, sandbox_report

    class FakeRenderer(SnapshotRendererPort):
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int]] = []

        def render(self, html: str, width_px: int, height_px: int) -> bytes | None:
            self.calls.append((html, width_px, height_px))
            return base64.b64decode(_PNG)

    renderer = FakeRenderer()
    src = "export default function Widget() { return <div/>; }"
    ref = "code/x/y.tsx#sha256:" + hashlib.sha256(src.encode()).hexdigest()
    evidence = MemoryEvidenceStore()
    report = sandbox_report()
    report["snapshot"] = "<div id='root'></div>"
    result = run_sandbox(
        run_id="y", source_ref=ref, manifest={"grid": "2x2", "capabilities": []},
        limits={"sandbox_hard_timeout_ms": 15000, "render_timeout_ms": 5000},
        source_store=FakeSourceStore({ref: src.encode()}),
        evidence_store=evidence,
        docker_runner=FakeDockerRunner(result=_command_result(report)),
        prefix="evidence/x/y", snapshot_renderer=renderer,
    )
    assert renderer.calls and renderer.calls[0][1:] == (160, 160)
    assert result.snapshot_ref is not None
    assert verify_evidence_refs(evidence, result.evidence_refs) is True


def _command_result(value: object) -> CommandResult:
    import json
    return CommandResult(stdout=json.dumps(value), stderr="", exit_code=0)


def test_real_chrome_renders_pixel_png() -> None:
    """真环境：本机 headless Chrome 把 DOM html 渲染成真像素 png（非 DOM 序列化）。

    无 Chrome 时 skip（环境守卫，不冒充通过）；有则断言 PNG magic + 尺寸合理。
    """

    from services.sandbox_runner.snapshot import PNG_MAGIC, ChromeSnapshotRenderer
    from services.sandbox_runner.snapshot import ChromeSnapshotRenderer as _R
    chrome = _R()._chrome
    if not chrome:
        import pytest
        pytest.skip("本机无 headless Chrome，像素渲染跳过（非回归）")

    renderer = ChromeSnapshotRenderer(chrome)
    assert renderer.available is True
    html = (
        "<html><body style='margin:0'>"
        "<div style='width:160px;height:160px;background:#e24b4a'>widget</div>"
        "</body></html>"
    )
    png = renderer.render(html, 320, 320)
    assert png is not None
    assert png[:8] == PNG_MAGIC
    assert len(png) > 100  # 真像素图不是空壳
