"""M08 代码执行（ADR-003 / Q5：Docker 执行；隔离机制暂缺显式登记）。

用 docker run 起一次性容器执行代码，代码经 stdin 传入（不落宿主文件）。
【未完成项】暂未实现沙箱隔离（网络关闭 / 资源限额 / 非 root / seccomp）——
当前 docker run 仅保证一次性（--rm），隔离策略待后续接入，禁止在生产处理不可信代码。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from packages.agent_core.errors import CoreError
from packages.contracts.enums import ReasonCode

_EXEC_IMAGE = "python:3.13-slim"
"""一次性执行镜像（Q5：用 Docker）。"""
_ISOLATION_NOTE = "none"
"""隔离状态标记：none=暂无沙箱机制（诚实登记，不伪装已隔离）。"""


@dataclass(slots=True, frozen=True)
class CodeExecResult:
    """代码执行结果。

    Attributes:
        ok: 退出码是否为 0。
        stdout: 标准输出。
        stderr: 标准错误。
        exit_code: 容器退出码。
        isolation_note: 隔离状态（none=暂未实现沙箱机制）。
    """

    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    isolation_note: str


def execute_code(code: str, timeout_s: int) -> CodeExecResult:
    """在一次性 Docker 容器中执行代码。

    Args:
        code: 待执行源码（stdin 传入，不落宿主文件）。
        timeout_s: 执行超时（秒）。

    Returns:
        CodeExecResult；退出码非 0 时 ok=False 并带 stderr。

    Raises:
        CoreError: 超时（infra，retryable）或其他 docker 调用故障。
    """
    cmd = ["docker", "run", "--rm", "-i", _EXEC_IMAGE, "python", "-"]
    try:
        proc = subprocess.run(
            cmd,
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CoreError.infra(ReasonCode.SANDBOX_UNAVAILABLE, "code execution timeout") from exc
    return CodeExecResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        isolation_note=_ISOLATION_NOTE,
    )
