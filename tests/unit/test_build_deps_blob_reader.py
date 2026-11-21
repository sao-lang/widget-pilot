"""ADR-003 未完成项4：build_deps 确实把 S3 reader 注入能力注册。"""
from __future__ import annotations

from services.orchestrator import capabilities as caps_mod
from services.orchestrator import deps as deps_mod


def test_build_deps_injects_s3_blob_reader(monkeypatch):
    """build_deps 调用 register_capabilities 时传入 S3 blob_reader（非本地路径）。"""
    captured: dict[str, object] = {}

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)

    # build_deps 函数体内 `from ...capabilities import register_capabilities`
    # 在调用时读模块属性，因此替换模块属性即可拦截。
    monkeypatch.setattr(caps_mod, "register_capabilities", spy)
    for key in ("MODEL_ENDPOINT", "MODEL_API_KEY", "MODEL_NAME"):
        monkeypatch.setenv(key, "test-value")

    deps_mod.build_deps("postgresql://x", "redis://x")

    assert "blob_reader" in captured, "build_deps 必须注入 blob_reader"
    reader = captured["blob_reader"]
    assert callable(reader)
    # 生产 reader 是 make_s3_blob_reader 的产物：无 hash 的 ref 必拒（fail-closed）
    try:
        reader("att/u/s/a/original")  # type: ignore[operator]
    except ValueError as exc:
        assert "sha256" in str(exc)
    else:
        raise AssertionError("生产 reader 未校验 hash，疑似仍是本地路径 reader")
