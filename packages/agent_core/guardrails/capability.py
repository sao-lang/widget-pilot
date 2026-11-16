"""M03 能力白名单（§11.2 / §4.1 L26）。

能力六项白名单（date/timer/local_storage/static_data/theme/locale）外的调用
一律 False；且调用的能力必须在 manifest.capabilities 中声明（声明式放行）。
"""
from __future__ import annotations

from packages.agent_core.types import CapabilityCall, Manifest
from packages.contracts.enums import Capability

CANONICAL_CAPS = {c.value for c in Capability}


def capability(call: CapabilityCall, manifest: Manifest) -> bool:
    """确定性放行：canonical 白名单内 且 manifest 已声明。"""
    name = call.name
    if name not in CANONICAL_CAPS:
        return False
    return name in manifest.capabilities
