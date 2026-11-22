"""M08 真像素快照渲染器（R8/R13）：把沙箱产出的 DOM 序列化 html 渲染成 png。

jsdom 只做 DOM 序列化无布局引擎，像素截图交给宿主机 headless Chrome（本机
已验证可用）。这是**渲染后端**而非执行后端：不可信代码已在一次性沙箱内跑过，
这里只把序列化后的 DOM 静态渲染，不执行 widget 逻辑、不访问网络。

缺 Chrome / 渲染失败 → render() 返回 None（降级：只有 html 结构证据、无 png），
绝不伪造 png，也不阻断主校验链（png 是证据增强，不是校验门禁）。生产由
worker 注入真实 Chrome 路径；测试桩在 tests/support/m08.py。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["PNG_MAGIC", "ChromeSnapshotRenderer", "SnapshotRendererPort"]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ENV_CHROME = "WIDGETPILOT_CHROME_PATH"
"""Chrome 可执行文件路径（12-factor）；未配置时按常见路径探测。"""

_CANDIDATES: tuple[str, ...] = (
    # Windows（Python 原生用 C:/ 盘符；git-bash 的 /c/ 路径 Path 判不到）
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    # Linux / 容器
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
)
_PNG_HEADER = PNG_MAGIC


@runtime_checkable
class SnapshotRendererPort(Protocol):
    """像素快照渲染端口（生产 ChromeSnapshotRenderer；测试桩在 tests/support）。"""

    def render(self, html: str, width_px: int, height_px: int) -> bytes | None:
        """把 DOM 序列化 html 渲染为 png；无可用渲染器返回 None（降级）。"""
        ...


class ChromeSnapshotRenderer:
    """真实像素渲染：headless Chrome 打开本地 html 文件并截图。

    Args:
        chrome_path: 显式可执行文件路径；None 时读 env 再按常见路径探测。
        timeout_s: 渲染超时（默认 15s，与沙箱硬超时一致）。
    """

    def __init__(self, chrome_path: str | None = None, timeout_s: int = 15) -> None:
        self._chrome = chrome_path or os.getenv(_ENV_CHROME, "") or self._discover()
        self._timeout_s = timeout_s

    @staticmethod
    def _discover() -> str:
        """按常见安装路径探测 Chrome/Edge；找不到返回空串（降级）。"""
        for candidate in _CANDIDATES:
            if Path(candidate).is_file():
                return candidate
        found = shutil.which("chromium") or shutil.which("google-chrome")
        return found or ""

    @property
    def available(self) -> bool:
        """是否有可用渲染器（供装配层记录缺口，不静默）。"""
        return bool(self._chrome) and Path(self._chrome).is_file()

    def render(self, html: str, width_px: int, height_px: int) -> bytes | None:
        """静态渲染 html → png bytes；失败/超时返回 None（不伪造证据）。"""
        if not self.available:
            return None
        width = max(1, min(int(width_px), 4096))
        height = max(1, min(int(height_px), 4096))
        with tempfile.TemporaryDirectory(prefix="wp-snap-") as tmp:
            src = Path(tmp) / "snapshot.html"
            out = Path(tmp) / "snapshot.png"
            src.write_text(
                "<!doctype html><meta charset='utf-8'>" + html,
                encoding="utf-8",
            )
            cmd = [
                self._chrome,
                "--headless=new", "--disable-gpu", "--no-sandbox",
                "--hide-scrollbars", "--disable-extensions", "--no-first-run",
                f"--screenshot={out.as_posix()}",
                f"--window-size={width},{height}",
                src.as_uri(),
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=self._timeout_s, check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if result.returncode != 0 or not out.is_file():
                return None
            data = out.read_bytes()
            return data if data[:8] == _PNG_HEADER else None
