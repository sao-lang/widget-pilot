"""M09 领域异常（编码规则 5：有限错误语义用具名异常，禁散落裸串）。

异常不抛穿到内核，由 REST 层折叠为统一错误信封 { code, message, traceId }；
本模块异常码对齐 §4.4 / §5.4 的 ReasonCode 映射。
"""
from __future__ import annotations


class BundleError(Exception):
    """M09 领域异常基类（§4.4 统一错误语义）。"""

    def __init__(self, code: str, message: str) -> None:
        """构造异常：code 对齐 ReasonCode，message 为可解释原因。"""
        super().__init__(message)
        self.code = code
        """业务错误码（ReasonCode 或 401/403/409 扩展码）。"""
        self.message = message
        """可展示的原因说明（禁只显示「失败」）。"""


class BundleEvidenceError(BundleError):
    """证据缺失或校验未过，拒绝成包（§1.7 证据先于结论 / A2）。"""

    def __init__(self, message: str = "证据先于结论：M08 校验未通过，拒绝成包") -> None:
        """默认码 CONTRACT_MISMATCH（§5.3 成包不一致）。"""
        super().__init__("CONTRACT_MISMATCH", message)


class BundleConflictError(BundleError):
    """状态冲突：DELETED 终态写操作或版本冲突（§5.4 HTTP 409）。"""

    def __init__(self, message: str = "状态冲突：DELETED 为终态，拒绝写操作") -> None:
        """默认码 CONTRACT_MISMATCH（§5.4 409 映射）。"""
        super().__init__("CONTRACT_MISMATCH", message)


class ConfirmRequiredError(BundleError):
    """删除缺少二次确认 token（§4.1 delete 请求体 / A13）。"""

    def __init__(self, message: str = "删除需二次确认：缺少或无效 confirm_token") -> None:
        """默认码 INPUT_INVALID（§5.4 HTTP 400）。"""
        super().__init__("INPUT_INVALID", message)


class BundleNotFoundError(BundleError):
    """包不存在（不泄漏他人资源是否存在，§2.3 ⑤）。"""

    def __init__(self, message: str = "产物不存在") -> None:
        """404 语义，错误码由 REST 层按 §5.4 映射。"""
        super().__init__("NOT_FOUND", message)


class BundleForbiddenError(BundleError):
    """越权访问他人包（§2.3 ⑤：一律 403，不泄漏存在性）。"""

    def __init__(self, message: str = "无权访问该产物") -> None:
        """403 语义，错误码由 REST 层按 §5.4 映射。"""
        super().__init__("FORBIDDEN", message)
