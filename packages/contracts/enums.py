# M02 enums.py —— 自动生成，禁止手改（单一事实来源 enums.source.json）
# 同源产物：enums.ts / enums.py 值集合必须完全一致（I2，CI contracts:check 校验）
from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    AWAITING_INPUT = "AWAITING_INPUT"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class BundleState(StrEnum):
    GENERATED = "GENERATED"
    INSTALLED = "INSTALLED"
    DELETED = "DELETED"

class GridSpec(StrEnum):
    _2X2 = "2x2"
    _2X4 = "2x4"
    _4X2 = "4x2"
    _4X4 = "4x4"
    _2X1 = "2x1"
    _1X2 = "1x2"

class WidgetType(StrEnum):
    CALENDAR = "calendar"
    CLOCK = "clock"
    ALARM = "alarm"
    TASK = "task"
    CUSTOM = "custom"

class AttachmentRole(StrEnum):
    VISUAL_REFERENCE = "visual_reference"
    CONTENT_EXTRACTION = "content_extraction"
    DATA_SOURCE = "data_source"
    STYLE_REFERENCE = "style_reference"

class Capability(StrEnum):
    DATE = "date"
    TIMER = "timer"
    LOCAL_STORAGE = "local_storage"
    STATIC_DATA = "static_data"
    THEME = "theme"
    LOCALE = "locale"

class ModelTier(StrEnum):
    CHEAP = "cheap"
    STANDARD = "standard"
    FRONTIER = "frontier"

class UserRole(StrEnum):
    USER = "user"
    CURATOR = "curator"

class AgentEventKind(StrEnum):
    RUN = "run"
    PLAN = "plan"
    SPEC = "spec"
    ATTACHMENT = "attachment"
    ROUTING = "routing"
    CODE = "code"
    GUARD = "guard"
    VALIDATION = "validation"
    SANDBOX = "sandbox"
    ARTIFACT = "artifact"
    COST = "cost"
    CHECKPOINT = "checkpoint"
    INTERRUPTED = "interrupted"
    NOTICE = "notice"
    ERROR = "error"
    DONE = "done"
    ASK_USER = "ask_user"
    REASONING = "reasoning"
    AGENT_MESSAGE = "agent_message"
    REVIEW = "review"
    DELEGATE = "delegate"

class ReasonCode(StrEnum):
    INPUT_INVALID = "INPUT_INVALID"
    INTENT_OUT_OF_SCOPE = "INTENT_OUT_OF_SCOPE"
    GUARD_INPUT_HIT = "GUARD_INPUT_HIT"
    GUARD_OUTPUT_HIT = "GUARD_OUTPUT_HIT"
    SPEC_CONFLICT = "SPEC_CONFLICT"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    COMPILE_FAILED = "COMPILE_FAILED"
    SANDBOX_TEST_FAILED = "SANDBOX_TEST_FAILED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    MODEL_ERROR = "MODEL_ERROR"
    SPEC_MISSING = "SPEC_MISSING"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CLARIFICATION_LIMIT = "CLARIFICATION_LIMIT"

class ContractPhase(StrEnum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    VERIFIED = "VERIFIED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    DEPRECATED = "DEPRECATED"

IMPORT_ALLOWLIST = ("react", "@widget/bridge")
EXPORT_DEFAULT = "Widget"
BRIDGE_MESSAGES = ("widget:ready", "widget:resize", "widget:error", "host:theme", "host:locale", "host:tick", "host:data")
IFRAME_SANDBOX = ("allow-scripts",)
CELL_PX = 80
MAX_ITER = 12
MAX_REPAIR = 2
MAX_CLARIFY = 2
Q1_401_403_409 = {"decision":"扩充枚举","codes":["UNAUTHORIZED","FORBIDDEN","CONFLICT"],"$comment":"M02 §9 Q1 定稿【推导·需评审】：扩充 3 个码进入 ReasonCode 值域之外的扩展集 http_reason_codes"}
Q2_ADMIN_ROLE = {"decision":"复用 curator 扩展能力","$comment":"M02 §9 Q2 定稿【推导·需评审】：UserRole 维持两角色，管理员能力由 curator 承载，/api/evals|metrics|audit 权限标注 curator"}
Q3_REFRESH_PATH = "POST /api/auth/refresh"
Q4_SSE_WINDOW_N = 500
Q4_LEASE_TTL_S = 30
Q4_HEARTBEAT_S = 10
Q4_STATUS = "影子校准中"
