"""M03 agent_core 常量（§6.3 / §4.5）。

门禁常量写死在代码里，无在线关闭开关（§1.7 #8）：
MAX_ITER / MAX_REPAIR / MAX_CLARIFY 直接复用 packages.contracts.enums
的单一事实来源定义，避免两处漂移。影子校准中的数值（TTL / 退避 / 窗口）
集中在本文件，便于 §9 Q2 定稿后一处修改。
"""
from __future__ import annotations

from packages.contracts.enums import MAX_CLARIFY, MAX_ITER, MAX_REPAIR  # noqa: F401

# §4.5 影子校准数值（【推导·需评审】，M03+M04+M11 影子期实测）
LEASE_TTL_S = 30           # 租约 TTL
HEARTBEAT_S = 10           # 心跳间隔
RETRY_MAX = 3              # RetryPolicy 最大重试次数（有上限）
RETRY_BASE_S = 0.5         # 指数退避基数
EVENT_WINDOW_N = 500       # Redis 事件窗口 N
SSE_HEARTBEAT_S = 15       # SSE 心跳间隔

# §3.4 F RRF 融合参数（【推导·需评审】，影子期校准）
RRF_K = 60
VECTOR_TOP_K = 10
BM25_TOP_K = 10
RERANK_TOP_K = 5

# 工具输出截断默认上限（ToolSpec.max_output_chars 缺省）
DEFAULT_MAX_OUTPUT_CHARS = 4000

# 四维预算默认硬上限（【推导·需评审】；M07/M11 可注入覆盖）
DEFAULT_BUDGET_LIMITS: dict[str, float] = {
    "tokens": 100_000,
    "iterations": MAX_ITER,
    "tool_calls": 50,
    "sandbox_minutes": 30.0,
}

# 四维预算维度名（顺序固定，用于断言与分账）
BUDGET_DIMENSIONS = ("tokens", "iterations", "tool_calls", "sandbox_minutes")

# 复现指纹组成（§6.4）
FINGERPRINT_PARTS = ("prompt_version", "recipe_version", "retrieval_snapshot", "model", "model_tier")
