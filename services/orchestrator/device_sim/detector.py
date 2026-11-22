"""ADR-010 §2.3 设备 API 调用序列检测（纯函数、零模型、零副作用）。

输入是沙箱 runner 上报的调用序列（时刻/API/返回码/参数/耗时），输出结构化违规
列表。约束值来自 `packages/contracts/device-apis.json` 单一事实来源（参考 Android
AppWidget/Glance 真实限制：getLocation 30s、scheduleRefresh 30min、全量渲染阈值、
限流后重试、10s ANR）。检测器只判定、不修复；重构建议随违规返回。

危险 API（白名单之外）按确定性名单判；不在此处调模型（模型兜底属 S2 前置校验）。
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast

from packages.agent_core.types import JsonValue

__all__ = [
    "DeviceCall",
    "DeviceLimits",
    "Violation",
    "detect_violations",
    "load_limits",
]

_CONTRACT = (
    Path(__file__).resolve().parents[3] / "packages" / "contracts" / "device-apis.json"
)


@dataclass(frozen=True, slots=True)
class DeviceCall:
    """一次设备 API 调用（沙箱注入的 mock bridge 上报）。"""

    api: str
    t_ms: int
    code: str = "OK"
    args: dict[str, JsonValue] | None = None
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class Violation:
    """一条违规：类别 + 证据 + 重构建议（确定性文本）。"""

    category: str
    api: str
    detail: str
    remediation: str


@dataclass(frozen=True, slots=True)
class DeviceLimits:
    """从契约加载的限流常量（不可变）。"""

    api_limits: dict[str, dict[str, JsonValue]]
    thresholds: dict[str, JsonValue]
    allowed_apis: frozenset[str]

    @property
    def anr_ms(self) -> int:
        return _as_int(self.thresholds.get("anr_task_ms"), 10000)

    @property
    def full_render_per_minute(self) -> int:
        return _as_int(self.thresholds.get("full_render_per_minute"), 10)

    @property
    def retry_window(self) -> int:
        return _as_int(self.thresholds.get("retry_window_calls"), 3)


def load_limits(path: Path | None = None) -> DeviceLimits:
    """从 device-apis.json 加载约束；文件缺失/非法即抛（fail-closed）。"""
    src = Path(path) if path else _CONTRACT
    raw = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("device-apis.json must be object")
    api_limits = cast("dict[str, dict[str, JsonValue]]", raw.get("api_limits", {}))
    thresholds = cast("dict[str, JsonValue]", raw.get("thresholds", {}))
    return DeviceLimits(
        api_limits=api_limits,
        thresholds=thresholds,
        allowed_apis=frozenset(api_limits) | {"device.render.update", "device.compute"},
    )


def _min_interval(limits: DeviceLimits, api: str) -> int:
    spec = limits.api_limits.get(api, {})
    return _as_int(spec.get("min_interval_ms"), 0) if isinstance(spec, dict) else 0


def _max_per_hour(limits: DeviceLimits, api: str) -> int:
    spec = limits.api_limits.get(api, {})
    return _as_int(spec.get("max_per_hour"), 0) if isinstance(spec, dict) else 0


def detect_violations(
    calls: tuple[DeviceCall, ...] | list[DeviceCall],
    limits: DeviceLimits | None = None,
) -> tuple[Violation, ...]:
    """检测五类违规，顺序稳定（调用序 × 规则序），便于断言与留证。

    规则：①白名单外 API=dangerous_api；②API 最小间隔/每小时配额违规=high_frequency；
    ③全量渲染超阈值=render_storm；④限流后短窗重试=retry_storm；⑤耗时>ANR=long_task。
    """
    cfg = limits or load_limits()
    out: list[Violation] = []
    by_api: dict[str, list[DeviceCall]] = defaultdict(list)
    for call in calls:
        by_api[call.api].append(call)

        if call.api not in cfg.allowed_apis:
            out.append(Violation(
                "dangerous_api", call.api,
                f"未声明设备 API: {call.api}",
                "改用 device-apis.json 白名单内的 API；禁止动态构造接口名",
            ))
        if call.duration_ms > cfg.anr_ms:
            out.append(Violation(
                "long_task", call.api,
                f"同步任务耗时 {call.duration_ms}ms > ANR {cfg.anr_ms}ms",
                "拆分任务或改用 WorkManager 语义延迟执行，勿阻塞主线程",
            ))

    for api, seq in by_api.items():
        if api == "device.render.update":
            fulls = sorted(c.t_ms for c in seq if (c.args or {}).get("mode") == "full")
            if len(fulls) > cfg.full_render_per_minute:
                out.append(Violation(
                    "render_storm", api,
                    f"1 分钟内全量渲染 {len(fulls)} 次 > 阈值 {cfg.full_render_per_minute}",
                    "改用 partiallyUpdateAppWidget 局部更新或降低刷新频率",
                ))
        else:
            interval = _min_interval(cfg, api)
            ordered = sorted(seq, key=lambda c: c.t_ms)
            for prev, cur in pairwise(ordered):
                if interval and cur.t_ms - prev.t_ms < interval:
                    out.append(Violation(
                        "high_frequency", api,
                        f"调用间隔 {cur.t_ms - prev.t_ms}ms < 最小 {interval}ms",
                        f"间隔至少 {interval}ms；用缓存或调度批量获取",
                    ))
            quota = _max_per_hour(cfg, api)
            if quota and len(ordered) > quota:
                out.append(Violation(
                    "high_frequency", api,
                    f"1 小时内调用 {len(ordered)} 次 > 配额 {quota}",
                    "减少调用频率或合并请求",
                ))

        # 限流后重试风暴
        ordered = sorted(seq, key=lambda c: c.t_ms)
        interval = _min_interval(cfg, api)
        for i, call in enumerate(ordered):
            if call.code == "RATE_LIMITED":
                window_end = call.t_ms + interval * cfg.retry_window if interval else call.t_ms + 3000
                retries = sum(1 for c in ordered[i + 1:] if c.t_ms <= window_end and c.code == "OK")
                if retries >= cfg.retry_window:
                    out.append(Violation(
                        "retry_storm", api,
                        f"限流后 {window_end - call.t_ms}ms 内重试 {retries} 次",
                        "退避重试（backoff）或等待下一维护窗口，勿忙等",
                    ))

    # scheduleRefresh < 30min：按参数判定
    for call in by_api.get("device.scheduleRefresh", []):
        ms = _as_int((call.args or {}).get("ms"), 0)
        minimum = _min_interval(cfg, "device.scheduleRefresh")
        if minimum and 0 < ms < minimum:
            out.append(Violation(
                "high_frequency", "device.scheduleRefresh",
                f"请求刷新间隔 {ms}ms < 系统下限 {minimum}ms",
                "设 updatePeriodMillis=0 并改用 WorkManager 调度",
            ))
    return tuple(out)


def _as_int(value: JsonValue | None, default: int) -> int:
    """JSON 值安全收窄为 int（非 int/bool 返回默认值）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else default
