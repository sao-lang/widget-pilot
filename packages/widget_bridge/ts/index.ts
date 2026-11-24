/**
 * M09/M16 宿主 ↔ 受限 iframe 的 postMessage 窄 bridge（§4.3.3 / §6.2）。
 *
 * 只放行 7 条固定消息，非任意 JSON-RPC；能力调用按 capability_bridge 白名单判定，
 * 未声明能力拒绝。时间统一由 host:tick 提供、本地存储按 storage_namespace 隔离。
 * 门禁值写死为常量，不提供在线关闭开关（§2.2）。
 */

/** 7 条窄消息名（§6.3，权威口径与 packages/contracts BRIDGE_MESSAGES 一致）。 */
export const BRIDGE_MESSAGES = [
  "widget:ready",
  "widget:resize",
  "widget:error",
  "host:theme",
  "host:locale",
  "host:tick",
  "host:data",
] as const;

/** 单条消息名联合类型。 */
export type BridgeMessage = (typeof BRIDGE_MESSAGES)[number];

/** 能力名（与契约 Capability 枚举值一致）。 */
export type Capability =
  | "date"
  | "timer"
  | "local_storage"
  | "static_data"
  | "theme"
  | "locale";

/** 控件 → 宿主消息 payload（§4.2）。 */
export type WidgetToHostPayload =
  | { kind: "widget:ready"; bundle_id: string; grid: string }
  | { kind: "widget:resize"; w: number; h: number }
  | { kind: "widget:error"; code: string; message: string; capability?: string };

/** 宿主 → 控件消息 payload（§4.2）。 */
export type HostToWidgetPayload =
  | { kind: "host:theme"; theme: "light" | "dark" }
  | { kind: "host:locale"; locale: string }
  | { kind: "host:tick"; ts: number; timezone: string }
  | { kind: "host:data"; namespace: string; payload: unknown };

/** iframe sandbox 定值（不含 allow-same-origin/top-navigation/forms/popups）。 */
export const IFRAME_SANDBOX = ["allow-scripts"] as const;

/**
 * 判定消息名是否在 7 条白名单内（A8：未知消息名拒绝）。
 */
export function assertMessageAllowed(kind: string): boolean {
  return (BRIDGE_MESSAGES as readonly string[]).includes(kind);
}

/**
 * 判定能力调用是否被运行时放行（A9：未声明能力拒绝）。
 */
export function isCapabilityAllowed(
  capability: Capability,
  capabilityBridge: ReadonlySet<Capability>,
): boolean {
  return capabilityBridge.has(capability);
}

/**
 * 校验一条宿主→控件消息：消息名白名单 + 能力声明（若带 capability 字段）。
 * 返回 null 表示放行，否则返回拒绝原因码。
 */
export function checkHostMessage(
  payload: HostToWidgetPayload & { capability?: Capability },
  capabilityBridge: ReadonlySet<Capability>,
): "CAPABILITY_DENIED" | "MESSAGE_DENIED" | null {
  if (!assertMessageAllowed(payload.kind)) return "MESSAGE_DENIED";
  const cap = payload.capability;
  if (cap !== undefined && !isCapabilityAllowed(cap, capabilityBridge)) {
    return "CAPABILITY_DENIED";
  }
  return null;
}
