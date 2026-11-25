"""请求级上下文（contextvars，并发安全）—— 生成路径、用户、会话。

- P1 ADR-019 D2：cost_sink 签名保持 ``Callable[[State], None]``（不改
  agent_core public API），generation_path 由 Dispatcher 分流时 set、worker
  落账时 get；默认 ``llm-only``。
- P2 #36：user_id / session_id 由 Dispatcher.process_one 在入口 set、**finally
  reset**（run_forever 同线程复用，不 reset 会跨任务串号——跨用户数据串号
  比 path 泄漏严重得多）；tool.session.* 能力 handler 从此处取 user_id，
  不进 args（守住防越权）。

所有 set 必须配对 reset（保存 token），不得裸 set。
例外：dispatcher._try_deterministic 内 generation_path_var 的多次 set 由
process_one 入口保存的 token 在 finally 统一复位（回到请求前值），语义等价
且避免跨函数传 token；新增此类用法必须同注释说明并由入口 token 兜底。
"""
from __future__ import annotations

from contextvars import ContextVar

__all__ = [
    "DEFAULT_GENERATION_PATH",
    "clarification_payload_var",
    "generation_path_var",
    "session_id_var",
    "user_id_var",
]

DEFAULT_GENERATION_PATH = "llm-only"
"""值域：deterministic / cloud-fallback / llm-only / llm-rework。"""

generation_path_var: ContextVar[str] = ContextVar(
    "widgetpilot_generation_path", default=DEFAULT_GENERATION_PATH
)
user_id_var: ContextVar[str] = ContextVar("widgetpilot_user_id", default="")
session_id_var: ContextVar[str] = ContextVar("widgetpilot_session_id", default="")
# P2 #36 B2：确定性澄清的精准反问载荷（序列化 JSON 串），由 _try_deterministic
# 写入、_write_back 落到 SessionRow.evidence_ref，供前端回读候选（无新迁移）。
clarification_payload_var: ContextVar[str] = ContextVar(
    "widgetpilot_clarification_payload", default=""
)
