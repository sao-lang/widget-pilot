/**
 * ADR-010 §2.1 设备 API 的 iframe ↔ 宿主 bridge 通道。
 *
 * 与 7 条窄消息（BRIDGE_MESSAGES）并列的独立设备通道：受限 iframe 内的 widget
 * 不能直接访问真实设备 API，须经 postMessage 请求宿主，宿主用 device-sim 判定后
 * 回传结果。设备 API 白名单/限流由 device-sim（单一事实来源 device-apis.json）负责，
 * 本文件只做消息搬运与请求/响应配对。
 *
 * 不接真实设备系统 API（ADR-010 §2.1 铁律）；未声明 API 由 device-sim 返回 DENIED。
 */
import type { DeviceResult, ResultCode } from "../../device_sim/ts/index.ts";

/** 设备通道消息名（与 7 条窄消息独立，不混入 BRIDGE_MESSAGES）。 */
export const DEVICE_CHANNEL = {
  CALL: "device:call",
  RESULT: "device:result",
} as const;

/** iframe → 宿主：一次设备 API 调用请求。 */
export interface DeviceCallMessage {
  kind: typeof DEVICE_CHANNEL.CALL;
  requestId: string;
  api: string;
  args: readonly unknown[];
}

/** 宿主 → iframe：调用结果（按 requestId 配对）。 */
export interface DeviceResultMessage {
  kind: typeof DEVICE_CHANNEL.RESULT;
  requestId: string;
  result: DeviceResult;
}

export type DeviceChannelMessage = DeviceCallMessage | DeviceResultMessage;

/** iframe 侧 postMessage 接口的最小形状（可注入，便于测试与 SSR）。 */
export interface MessageSink {
  postMessage(message: unknown, targetOrigin: string): void;
}

/** 判定一条消息是否为设备调用请求（fail-closed：字段不全返回 false）。 */
export function isDeviceCall(value: unknown): value is DeviceCallMessage {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    v.kind === DEVICE_CHANNEL.CALL &&
    typeof v.requestId === "string" &&
    typeof v.api === "string" &&
    Array.isArray(v.args)
  );
}

/** 判定一条消息是否为设备调用结果。 */
export function isDeviceResult(value: unknown): value is DeviceResultMessage {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    v.kind === DEVICE_CHANNEL.RESULT &&
    typeof v.requestId === "string" &&
    typeof v.result === "object" &&
    v.result !== null
  );
}

/**
 * iframe 侧调用代理：把 window.widgetDevice.* 调用转成 postMessage 请求，
 * 等待宿主回传的 device:result（按 requestId 配对）。
 *
 * 生产由宿主注入 device-sim 后，widget 只看到这个代理，不接触真实设备 API。
 */
export function createDeviceProxy(
  sink: MessageSink,
  targetOrigin: string,
  onResult: (requestId: string, result: DeviceResult) => void,
  newRequestId: () => string = () => Math.random().toString(36).slice(2),
) {
  const pending = new Map<string, (result: DeviceResult) => void>();

  /** 宿主回传分发（宿主收到 device:result 时调用）。 */
  function receive(value: unknown): boolean {
    if (!isDeviceResult(value)) return false;
    const resolve = pending.get(value.requestId);
    if (!resolve) return false; // 未知 requestId 忽略（防伪造/重复回包）
    pending.delete(value.requestId);
    onResult(value.requestId, value.result);
    resolve(value.result);
    return true;
  }

  /** 发起一次设备调用（返回 Promise，等待宿主回传）。 */
  function call(api: string, args: readonly unknown[]): Promise<DeviceResult> {
    const requestId = newRequestId();
    return new Promise((resolve) => {
      pending.set(requestId, resolve);
      sink.postMessage(
        { kind: DEVICE_CHANNEL.CALL, requestId, api, args } satisfies DeviceCallMessage,
        targetOrigin,
      );
    });
  }

  return { call, receive, pendingCount: () => pending.size };
}

/** 结果码枚举运行时校验（与契约 result_codes 一致，未知码 fail-closed）。 */
export function isResultCode(code: unknown): code is ResultCode {
  return (
    code === "OK" ||
    code === "RATE_LIMITED" ||
    code === "DOZE_DEFERRED" ||
    code === "QUOTA_EXCEEDED" ||
    code === "DENIED"
  );
}
