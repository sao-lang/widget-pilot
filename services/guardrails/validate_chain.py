"""M06 校验链装配入口的再导出（实现在 services/orchestrator/validate_chain.py）。

按 §2.4 代码落位，装配入口归 orchestrator；此模块提供 guardrails 侧便捷导入。
"""

from __future__ import annotations

from services.orchestrator.validate_chain import validate_chain

__all__ = ["validate_chain"]
