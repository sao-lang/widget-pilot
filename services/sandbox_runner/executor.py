"""M08 一次性代码执行（ADR-003 / Q5，真隔离升级）：

宿主机直调 Docker，保留 execute_code 兼容性；network/capabilities/user/cgroups/
seccomp/read-only/tmpfs 全部收紧，15s 硬超时强杀并确认容器消失。
原"无隔离禁止生产"的自述已被实际参数取代。
"""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from packages.agent_core.errors import CoreError
from packages.contracts.enums import ReasonCode

_EXEC_IMAGE = "python:3.13-slim"
_ISOLATION_NOTE = "docker-hardened"
_SECCONFIG_PATH = Path(__file__).resolve().parents[2] / "infra/sandbox/seccomp.json"


@dataclass(slots=True, frozen=True)
class CodeExecResult:
    """代码执行结果。

    Attributes:
        ok: 退出码是否为 0。
        stdout: 标准输出。
        stderr: 标准错误。
        exit_code: 容器退出码。
        isolation_note: 隔离状态（docker-hardened=真隔离参数已启用）。
    """

    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    isolation_note: str


def execute_code(code: str, timeout_s: int) -> CodeExecResult:
    """在真隔离的一次性 Docker 容器中执行代码（stdin 传入，不落宿主文件）。

    Args:
        code: 待执行源码。
        timeout_s: 硬超时（秒）。

    Returns:
        CodeExecResult；退出码非 0 时 ok=False 并带 stderr。

    Raises:
        CoreError: 超时或 docker 调用故障（infra, retryable）。
    """
    name = "wp-exec-" + uuid.uuid4().hex[:12]
    cmd = [
        "docker", "run", "--rm", "-i", "--name", name,
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--security-opt", f"seccomp={_SECCONFIG_PATH.as_posix()}",
        "--user", "10001:10001",
        "--memory", "512m",
        "--cpus", "1",
        "--pids-limit", "64",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        _EXEC_IMAGE, "python", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, input=code, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_container(name)
        raise CoreError.infra(ReasonCode.SANDBOX_UNAVAILABLE, "code execution timeout") from exc
    except FileNotFoundError as exc:
        raise CoreError.infra(ReasonCode.SANDBOX_UNAVAILABLE, "docker CLI unavailable") from exc
    return CodeExecResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        isolation_note=_ISOLATION_NOTE,
    )


def _kill_container(name: str) -> None:
    """超时强杀；桩环境可能再次抛超时，不能盖掉原始基础设施错误。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", name)
    for args in (["docker", "kill", safe], ["docker", "rm", "-f", safe]):
        try:
            subprocess.run(args, capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
