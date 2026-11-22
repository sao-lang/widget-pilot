"""ADR-010 设备模拟环境调用序列检测单测（零模型、确定性）。

验证限流/渲染风暴/重试风暴/大任务/危险 API 五类门禁；纯函数，不依赖浏览器。
"""
from __future__ import annotations

from services.orchestrator.device_sim.detector import (
    DeviceCall,
    detect_violations,
    load_limits,
)


def _call(api: str, t: int, code: str = "OK", **kw: object) -> DeviceCall:
    return DeviceCall(api=api, t_ms=t, code=code, **kw)  # type: ignore[arg-type]


def test_limits_loaded_from_contract():
    limits = load_limits()
    assert limits.api_limits["device.getLocation"]["min_interval_ms"] == 30000
    assert limits.api_limits["device.scheduleRefresh"]["min_interval_ms"] == 1800000
    assert limits.thresholds["anr_task_ms"] == 10000


def test_no_calls_no_violation_zero_regression():
    assert detect_violations(()) == ()


def test_frequent_location_call_rejected():
    calls = (_call("device.getLocation", 0), _call("device.getLocation", 5000))
    v = detect_violations(calls)
    assert any("high_frequency" in x.category for x in v)


def test_location_respecting_interval_passes():
    calls = (_call("device.getLocation", 0), _call("device.getLocation", 31000))
    assert detect_violations(calls) == ()


def test_schedule_refresh_below_30min_rejected():
    v = detect_violations((_call("device.scheduleRefresh", 0, args={"ms": 60000}),))
    assert any("high_frequency" in x.category or "dangerous_api" in x.category for x in v)


def test_render_storm_detected():
    # 1 分钟内 11 次全量渲染 > 阈值 10
    calls = tuple(_call("device.render.update", i * 5000, args={"mode": "full"}) for i in range(11))
    v = detect_violations(calls)
    assert any("render_storm" in x.category for x in v)


def test_partial_render_not_storm():
    calls = tuple(_call("device.render.update", i * 1000, args={"mode": "partial"}) for i in range(30))
    assert not any("render_storm" in x.category for x in detect_violations(calls))


def test_retry_storm_detected():
    calls = (
        _call("device.getBattery", 0, code="RATE_LIMITED"),
        _call("device.getBattery", 1000),
        _call("device.getBattery", 2000),
        _call("device.getBattery", 3000),
    )
    v = detect_violations(calls)
    assert any("retry_storm" in x.category for x in v)


def test_long_task_detected():
    v = detect_violations((_call("device.compute", 0, duration_ms=12000),))
    assert any("long_task" in x.category for x in v)


def test_undeclared_dynamic_api_is_danger():
    v = detect_violations((_call("window.evilApi", 0),))
    assert any("dangerous_api" in x.category for x in v)


def test_run_sandbox_flags_device_violations() -> None:
    """ADR-010：沙箱报告含 device_calls 时，违规转 SANDBOX_TEST_FAILED 并留证。"""
    import hashlib

    from services.sandbox_runner.entry import run_sandbox
    from services.sandbox_runner.ports import CommandResult
    from tests.support.m08 import FakeDockerRunner, FakeSourceStore, MemoryEvidenceStore

    src = "export default function Widget() { return <div/>; }"
    ref = "code/s/r.tsx#sha256:" + hashlib.sha256(src.encode()).hexdigest()
    report = {
        "stages": {
            "compile": {"compiled": "var x=1;"},
            "render": {"grid": "2x2", "width_px": 160, "height_px": 160,
                       "max_child_width_px": 100, "max_child_height_px": 100, "errors": []},
            "bridge": {"events": []},
        },
        "interactions": [],
        "logs": "",
        "snapshot": "<div/>",
        "device_calls": [
            {"api": "device.getLocation", "t_ms": 0, "code": "OK"},
            {"api": "device.getLocation", "t_ms": 1000, "code": "OK"},
        ],
    }
    evidence = MemoryEvidenceStore()
    result = run_sandbox(
        run_id="r", source_ref=ref,
        manifest={"grid": "2x2", "capabilities": []},
        limits={"sandbox_hard_timeout_ms": 15000, "render_timeout_ms": 5000},
        source_store=FakeSourceStore({ref: src.encode()}),
        evidence_store=evidence,
        docker_runner=FakeDockerRunner(result=CommandResult(
            stdout=__import__("json").dumps(report), stderr="", exit_code=0)),
        prefix="evidence/s/r",
    )
    assert result.passed is False
    assert any(c.name == "device_api_safe" and not c.passed for c in result.cases)
