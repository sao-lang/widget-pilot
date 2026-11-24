"""M08 代码执行 capability（ADR-003 / Q5：Docker 执行、隔离机制暂缺）测试。"""
from __future__ import annotations

import subprocess

import pytest

from services.sandbox_runner.executor import CodeExecResult, execute_code


def test_execute_code_builds_docker_run_command(monkeypatch):
    """真实调用 docker CLI 跑代码；命令含 --rm 与镜像，代码经 stdin 传入。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "42\n", "")

    monkeypatch.setattr("services.sandbox_runner.executor.subprocess.run", fake_run)
    result = execute_code("print(42)", timeout_s=5)
    assert result.ok is True
    assert result.stdout == "42\n"
    assert calls[0][0] == "docker"
    assert "--rm" in calls[0]
    assert "python:3.13-slim" in calls[0]


def test_execute_code_reports_nonzero(monkeypatch):
    """退出码非 0 → ok=False，带 stderr（不静默成功）。"""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "SyntaxError")

    monkeypatch.setattr("services.sandbox_runner.executor.subprocess.run", fake_run)
    result = execute_code("bad(", timeout_s=5)
    assert result.ok is False
    assert "SyntaxError" in result.stderr


def test_execute_code_timeout_is_infra(monkeypatch):
    """超时抛基础设施故障（retryable），不吞成业务成功。"""
    from packages.agent_core.errors import CoreError

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr("services.sandbox_runner.executor.subprocess.run", fake_run)
    with pytest.raises(CoreError) as ei:
        execute_code("while True: pass", timeout_s=5)
    assert ei.value.retryable is True


def test_execute_code_result_dataclass_frozen():
    """CodeExecResult 不可变，防止执行后被篡改。"""
    r = CodeExecResult(ok=True, stdout="x", stderr="", exit_code=0, isolation_note="none")
    assert r.isolation_note == "none"


def test_container_cli_podman_switch(monkeypatch):
    """容器 CLI 可切 podman（默认 docker），命令前缀正确（docker 卸载后可用 podman）。"""
    from services.sandbox_runner import container_cli

    monkeypatch.delenv("WIDGETPILOT_CONTAINER_CLI_PATH", raising=False)
    monkeypatch.delenv("WIDGETPILOT_CONTAINER_CLI", raising=False)
    assert container_cli.cli() == "docker"
    assert container_cli.run_command(["run", "--rm"]) == ["docker", "run", "--rm"]

    monkeypatch.setenv("WIDGETPILOT_CONTAINER_CLI", "podman")
    assert container_cli.cli() == "podman"
    assert container_cli.run_command(["run", "--rm"]) == ["podman", "run", "--rm"]
    assert container_cli.info_probe()[0] == "podman"

    # 绝对路径优先（Windows podman 常不在 PATH）
    monkeypatch.setenv("WIDGETPILOT_CONTAINER_CLI_PATH", "D:/apps/podman/podman.exe")
    assert container_cli.cli() == "D:/apps/podman/podman.exe"


def test_execute_code_uses_configured_cli(monkeypatch):
    """execute_code 命令前缀随容器 CLI 配置变化（默认 docker）。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr("services.sandbox_runner.executor.subprocess.run", fake_run)
    monkeypatch.setenv("WIDGETPILOT_CONTAINER_CLI", "podman")
    monkeypatch.delenv("WIDGETPILOT_CONTAINER_CLI_PATH", raising=False)
    execute_code("print('ok')", timeout_s=5)
    assert calls[0][0] == "podman"
    assert calls[0][1] == "run"
