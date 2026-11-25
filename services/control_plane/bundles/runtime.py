"""M09 渲染协议生成（§4.3.3 / §6.3 / A6/A7/A17）。

确定性纯函数：card.px 六种规格真实比例、严格 CSP、sandbox token、
capability_bridge 交集、limits 上限——同一输入永远产出同一协议（可断言可回归）。
门禁值写死为常量，不提供在线关闭开关（§2.2 / 原则 10）。
"""
from __future__ import annotations

from packages.contracts.enums import Capability, GridSpec
from packages.contracts.types import JsonObject, JsonValue
from services.control_plane.bundles.bridge import (
    BRIDGE_MESSAGES,
    GLOBAL_CAPABILITY_ALLOWLIST,
)
from services.control_plane.bundles.domain import (
    BridgePolicy,
    CardRuntime,
    IframeRuntime,
    RuntimeLimits,
    WidgetRuntime,
)

CARD_CELL_PX: int = 80
"""网格单元像素（§6.3，cell=80px）。"""

GRID_PX: dict[GridSpec, tuple[int, int]] = {
    GridSpec._2X1: (160, 80),
    GridSpec._1X2: (80, 160),
    GridSpec._2X2: (160, 160),
    GridSpec._2X4: (160, 320),
    GridSpec._4X2: (320, 160),
    GridSpec._4X4: (320, 320),
    # v2fix：新增 4 规格，按 cell=80px 计算（与 sandbox_runner/render.py 一致）
    GridSpec._2X3: (160, 240),
    GridSpec._3X2: (240, 160),
    GridSpec._4X3: (320, 240),
    GridSpec._5X3: (400, 240),
}
"""规格 → (w,h) 真实像素映射（§6.3 / A17，按 cell=80 计算，不统一拉伸）。"""

STRICT_CSP: str = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)
"""严格 CSP（§6.3 推导串）：禁外联脚本 / 禁 eval / 禁网络，不含 unsafe-eval。"""

IFRAME_SANDBOX: tuple[str, ...] = ("allow-scripts",)
"""sandbox 定值（§2.4 #4）：不含 allow-same-origin/top-navigation/forms/popups。"""

RENDER_TIMEOUT_MS: int = 5000
"""iframe 渲染超时熔断（§6.3 推导，影子期校准）。"""

MAX_MESSAGE_RATE: int = 60
"""bridge 消息速率上限，条/秒（§6.3 推导）。"""


def card_px(grid: GridSpec) -> tuple[int, int]:
    """按规格返回真实像素 (w,h)（A17）。未知 grid 显式回退 CUSTOM 尺寸（规则 5）。"""
    return GRID_PX.get(grid, (CARD_CELL_PX * 2, CARD_CELL_PX * 2))


def build_csp() -> str:
    """返回确定性严格 CSP（A7：不可在线关闭，模板常量）。"""
    return STRICT_CSP


def build_capability_bridge(
    manifest: JsonObject,
    global_allowlist: set[Capability] | frozenset[Capability] | None = None,
) -> list[Capability]:
    """计算 capability_bridge = manifest.capabilities ∩ 全局白名单（A6/I5）。

    Args:
        manifest: 产物声明（``capabilities`` 为能力字符串数组）。
        global_allowlist: 平台策略白名单；None 用默认全集（Capability 全集）。

    Returns:
        放行能力列表（保序去重），未声明或不在白名单的剔除。
    """
    allowlist = global_allowlist if global_allowlist is not None else GLOBAL_CAPABILITY_ALLOWLIST
    declared: JsonValue = manifest.get("capabilities", [])
    result: list[Capability] = []
    if isinstance(declared, list):
        for raw in declared:
            if not isinstance(raw, str):
                continue
            try:
                cap = Capability(raw)
            except ValueError:
                # 未知能力显式剔除，不放行（I5 fail-closed）。
                continue
            if cap in allowlist and cap not in result:
                result.append(cap)
    return result


def build_runtime(
    *,
    grid: GridSpec,
    srcdoc_ref: str,
    manifest: JsonObject,
    storage_namespace: str,
    global_allowlist: set[Capability] | frozenset[Capability] | None = None,
) -> WidgetRuntime:
    """组装完整 WidgetRuntime（§3.2 阶段 B / R5）。"""
    w, h = card_px(grid)
    caps = build_capability_bridge(manifest, global_allowlist)
    return WidgetRuntime(
        card=CardRuntime(grid=grid, w=w, h=h),
        iframe=IframeRuntime(
            sandbox=IFRAME_SANDBOX, csp=build_csp(), srcdoc_ref=srcdoc_ref
        ),
        bridge=BridgePolicy(
            messages=BRIDGE_MESSAGES,
            capability_bridge=tuple(caps),
            storage_namespace=storage_namespace,
        ),
        limits=RuntimeLimits(
            render_timeout_ms=RENDER_TIMEOUT_MS, max_message_rate=MAX_MESSAGE_RATE
        ),
    )
