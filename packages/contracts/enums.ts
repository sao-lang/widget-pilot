// M02 enums.ts —— 自动生成，禁止手改（单一事实来源 enums.source.json）
// 同源产物：enums.ts / enums.py 值集合必须完全一致（I2，CI contracts:check 校验）

export type SessionState =
  | "DRAFT" | "RUNNING" | "PAUSED" | "AWAITING_INPUT" | "VALIDATING" | "COMPLETED" | "FAILED" | "CANCELLED";

export type BundleState =
  | "GENERATED" | "INSTALLED" | "DELETED";

export type GridSpec =
  | "2x2" | "2x4" | "4x2" | "4x4" | "2x1" | "1x2";

export type WidgetType =
  | "calendar" | "clock" | "alarm" | "task" | "custom";

export type AttachmentRole =
  | "visual_reference" | "content_extraction" | "data_source" | "style_reference";

export type Capability =
  | "date" | "timer" | "local_storage" | "static_data" | "theme" | "locale";

export type ModelTier =
  | "cheap" | "standard" | "frontier";

export type UserRole =
  | "user" | "curator";

export type AgentEventKind =
  | "run" | "plan" | "spec" | "attachment" | "skill" | "routing" | "code" | "guard" | "validation" | "sandbox" | "artifact" | "cost" | "checkpoint" | "interrupted" | "notice" | "error" | "done";

export type ReasonCode =
  | "INPUT_INVALID" | "INTENT_OUT_OF_SCOPE" | "GUARD_INPUT_HIT" | "GUARD_OUTPUT_HIT" | "SPEC_CONFLICT" | "CAPABILITY_DENIED" | "CONTRACT_MISMATCH" | "COMPILE_FAILED" | "SANDBOX_TEST_FAILED" | "RETRY_EXHAUSTED" | "BUDGET_EXHAUSTED" | "SANDBOX_UNAVAILABLE" | "HUMAN_REQUIRED" | "MODEL_ERROR";

export type ContractPhase =
  | "DRAFT" | "GENERATED" | "VERIFIED" | "PUBLISHED" | "REJECTED" | "DEPRECATED";

export const IMPORT_ALLOWLIST = ["react","@widget/bridge"] as const;
export const EXPORT_DEFAULT = "Widget" as const;
export const BRIDGE_MESSAGES = ["widget:ready","widget:resize","widget:error","host:theme","host:locale","host:tick","host:data"] as const;
export const IFRAME_SANDBOX = ["allow-scripts"] as const;
export const CELL_PX = 80 as const;
export const MAX_ITER = 12 as const;
export const MAX_REPAIR = 2 as const;
export const Q1_401_403_409 = {"decision":"扩充枚举","codes":["UNAUTHORIZED","FORBIDDEN","CONFLICT"],"$comment":"M02 §9 Q1 定稿【推导·需评审】：扩充 3 个码进入 ReasonCode 值域之外的扩展集 http_reason_codes"} as const;
export const Q2_ADMIN_ROLE = {"decision":"复用 curator 扩展能力","$comment":"M02 §9 Q2 定稿【推导·需评审】：UserRole 维持两角色，管理员能力由 curator 承载，/api/evals|metrics|audit 权限标注 curator"} as const;
export const Q3_REFRESH_PATH = "POST /api/auth/refresh" as const;
export const Q4_SSE_WINDOW_N = 500 as const;
export const Q4_LEASE_TTL_S = 30 as const;
export const Q4_HEARTBEAT_S = 10 as const;
export const Q4_STATUS = "影子校准中" as const;
