"""M03 库级统一错误与分类（§4.4，【推导·需评审】已固化）。

业务失败 ≠ 基础设施故障：
- 业务失败 → REPAIR 回边 / 终态，不进 RetryPolicy；
- 基础设施故障 → RetryPolicy 指数退避（有上限）/ 降级。

分类判据不是新的 ReasonCode，只是把既有 ReasonCode 分成两类。
v1.3 新增的 SPEC_MISSING / CLARIFICATION_REQUIRED / CLARIFICATION_LIMIT
归入业务失败。
"""
from __future__ import annotations

from packages.contracts.enums import ReasonCode

# 业务失败：出护栏 / 契约 / 沙箱 / final schema 失败、回边超限、预算触顶
BUSINESS_FAILURE: frozenset[str] = frozenset({
    "GUARD_INPUT_HIT", "GUARD_OUTPUT_HIT", "SPEC_CONFLICT",
    "CAPABILITY_DENIED", "CONTRACT_MISMATCH", "COMPILE_FAILED",
    "SANDBOX_TEST_FAILED", "RETRY_EXHAUSTED", "BUDGET_EXHAUSTED",
    "INPUT_INVALID", "INTENT_OUT_OF_SCOPE", "HUMAN_REQUIRED",
    # v1.3 ADR-001：规格获取与澄清相关归入业务失败
    "SPEC_MISSING", "CLARIFICATION_REQUIRED", "CLARIFICATION_LIMIT",
})

# 基础设施故障：模型 429/5xx、网络抖动、沙箱启动失败（走有上限退避）
INFRA_FAILURE: frozenset[str] = frozenset({
    "MODEL_ERROR", "SANDBOX_UNAVAILABLE",
})


class CoreError(Exception):
    """库级异常，向上抛给调用方服务（M04 在 REST/SSE 边界映射 HTTP）。

    本内核不产生 HTTP 响应；code 即 ReasonCode。
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        evidence_ref: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.evidence_ref = evidence_ref
        # 未显式指定时按分类自动判定；未知码默认不可重试
        self.retryable = (code in INFRA_FAILURE) if retryable is None else retryable
        super().__init__(self.message)

    @classmethod
    def business(cls, code: str, message: str = "", evidence_ref: str | None = None) -> CoreError:
        return cls(code, message, evidence_ref, retryable=False)

    @classmethod
    def infra(cls, code: str, message: str = "", evidence_ref: str | None = None) -> CoreError:
        return cls(code, message, evidence_ref, retryable=True)

    def __repr__(self) -> str:
        return f"CoreError(code={self.code!r}, retryable={self.retryable!r}, evidence_ref={self.evidence_ref!r})"


def classify(code: str) -> str:
    """返回 'business' / 'infra' / 'unknown'，供断言与工具错误分类使用。"""
    if code in BUSINESS_FAILURE:
        return "business"
    if code in INFRA_FAILURE:
        return "infra"
    return "unknown"


def assert_partition(codes: object) -> bool:
    """T15：BUSINESS_FAILURE 与 INFRA_FAILURE 并集 = ReasonCode 全集，交集为空。"""
    all_codes = {c.value for c in ReasonCode}
    union = BUSINESS_FAILURE | INFRA_FAILURE
    overlap = BUSINESS_FAILURE & INFRA_FAILURE
    return union == all_codes and not overlap
