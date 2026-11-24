"""容器 CLI 单一事实来源（沙箱执行器 / 测试守卫共用）。

沙箱生产在过去硬编码 ``docker``；用户环境已卸载 docker、改用 podman
（CLI 在 D:\\apps\\podman，后端 crun 可跑真容器）。本模块把 CLI 名收敛到一处：
默认 ``docker`` 保持既有行为，设 ``WIDGETPILOT_CONTAINER_CLI=podman`` 即切 podman。
不引入第三方依赖、不降级为 fake——CLI 不存在时由调用方 fail-closed。
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

_ENV_CLI = "WIDGETPILOT_CONTAINER_CLI"
_DEFAULT_CLI = "docker"
"""默认仍为 docker（既有部署零回归）；podman 环境显式设环境变量。"""

# podman 在 Windows 常以绝对路径安装且不在工具会话 PATH 中；支持显式路径覆盖。
_ENV_CLI_PATH = "WIDGETPILOT_CONTAINER_CLI_PATH"


def cli() -> str:
    """返回容器 CLI 名或绝对路径（读环境变量，默认 docker）。

    优先 ``WIDGETPILOT_CONTAINER_CLI_PATH``（绝对路径），其次
    ``WIDGETPILOT_CONTAINER_CLI``（名，如 podman），最后默认 docker。
    """
    explicit = os.getenv(_ENV_CLI_PATH, "").strip()
    if explicit:
        return explicit
    return os.getenv(_ENV_CLI, _DEFAULT_CLI).strip() or _DEFAULT_CLI


def available() -> bool:
    """CLI 是否可执行（绝对路径用 exists，名字用 shutil.which）。"""
    candidate = cli()
    if Path(candidate).is_absolute():
        return Path(candidate).is_file()
    return shutil.which(candidate) is not None


def run_command(args: Sequence[str]) -> list[str]:
    """给一组容器子命令加上 CLI 前缀（如 ``['run', ...]`` → ``['podman','run',...]``）。"""
    return [cli(), *args]


def info_probe() -> list[str]:
    """返回探测容器后端可用性的命令。

    不用 ``--format {{.ServerVersion}}``：该模板字段 docker 有、podman 无
    （podman infoReport 无 ServerVersion 字段，会 rc=125）。裸 ``info`` 两边都返回 0。
    """
    return [cli(), "info"]
