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
import {
  createDeviceSim,
  type DeviceResult,
  type DeviceSimOptions,
  type ResultCode,
} from "../../device_sim/ts/index.ts";

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

/**
 * 宿主侧 API 派发表：把 device:call 的 api 名映射到 device_sim 的具体方法。
 *
 * 显式列出而非 eval/动态索引（ADR-010 §3.1：禁动态构造接口名）。未列出的 API
 * 一律 DENIED，不落到模拟运行时——白名单由 device_sim 内部再做一次，双层门禁。
 */
type DeviceApiName =
  | "device.getLocation"
  | "device.getBattery"
  | "device.getNetworkStatus"
  | "device.sendNotification"
  | "device.scheduleRefresh"
  | "device.storage.write"
  | "device.storage.read"
  | "device.render.update"
  | "device.compute";

/** device_sim.device 的最小形状（避免强绑具体包类型，鸭子协议）。 */
interface DeviceLike {
  getLocation(): DeviceResult;
  getBattery(): DeviceResult;
  getNetworkStatus(): DeviceResult;
  sendNotification(message: string): DeviceResult;
  scheduleRefresh(ms: number): DeviceResult;
  storage: { write(key: string, value: string): DeviceResult; read(key: string): DeviceResult };
  render: { update(mode: "full" | "partial"): DeviceResult };
  compute<T>(fn: () => T): DeviceResult<T>;
}

/** 把设备 API 名 + 参数派发到模拟运行时的具体方法；未知 API fail-closed DENIED。 */
export function invokeDeviceApi(
  device: DeviceLike,
  api: string,
  args: readonly unknown[],
): DeviceResult {
  switch (api as DeviceApiName) {
    case "device.getLocation":
      return device.getLocation();
    case "device.getBattery":
      return device.getBattery();
    case "device.getNetworkStatus":
      return device.getNetworkStatus();
    case "device.sendNotification":
      return device.sendNotification(String(args[0] ?? ""));
    case "device.scheduleRefresh":
      return device.scheduleRefresh(typeof args[0] === "number" ? args[0] : 0);
    case "device.storage.write":
      return device.storage.write(String(args[0] ?? ""), String(args[1] ?? ""));
    case "device.storage.read":
      return device.storage.read(String(args[0] ?? ""));
    case "device.render.update":
      return device.render.update(args[0] === "full" ? "full" : "partial");
    case "device.compute":
      return device.compute(() => args[0] ?? null);
    default:
      return { ok: false, code: "DENIED" };
  }
}

/**
 * 宿主侧 handler：收到 device:call → 调 device_sim → 回传 device:result。
 *
 * 宿主在 iframe 的 message 监听里调用本函数；返回 true 表示消息已被本通道消费。
 * 只接受来自 iframe 的 device:call，不处理 device:result（那是 iframe 侧收的）。
 */
export function createHostDeviceHandler(
  device: DeviceLike,
  sink: MessageSink,
  targetOrigin: string,
) {
  return function handle(value: unknown): boolean {
    if (!isDeviceCall(value)) return false;
    const result = invokeDeviceApi(device, value.api, value.args);
    sink.postMessage(
      { kind: DEVICE_CHANNEL.RESULT, requestId: value.requestId, result } satisfies DeviceResultMessage,
      targetOrigin,
    );
    return true;
  };
}

/**
 * iframe 侧 widget 看到的 window.widgetDevice 对象：每个具体方法走 proxy.call
 * 发 postMessage，返回 Promise<DeviceResult>（等待宿主回传）。
 *
 * 这样 widget 源码里的 `window.widgetDevice.getBattery()` 无需改动即被桥接到宿主；
 * 本地不接触真实设备 API（ADR-010 §2.1 铁律）。
 */
export function createWidgetDeviceProxy(proxy: ReturnType<typeof createDeviceProxy>) {
  const call = (api: string, args: readonly unknown[]) => proxy.call(api, args);
  return {
    getLocation: () => call("device.getLocation", []),
    getBattery: () => call("device.getBattery", []),
    getNetworkStatus: () => call("device.getNetworkStatus", []),
    sendNotification: (message: string) => call("device.sendNotification", [message]),
    scheduleRefresh: (ms: number) => call("device.scheduleRefresh", [ms]),
    storage: {
      write: (key: string, value: string) => call("device.storage.write", [key, value]),
      read: (key: string) => call("device.storage.read", [key]),
    },
    render: { update: (mode: "full" | "partial") => call("device.render.update", [mode]) },
    compute: <T>(fn: () => T) => call("device.compute", [fn()]),
  };
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

/**
 * 宿主侧一键装配：创建 device_sim + 监听 iframe 的 device:call + 回传 device:result。
 *
 * M16 渲染控件 iframe 时调用一次即可，无需自己接线；返回句柄用于审计取证、
 * Doze 切换与卸载时移除监听（避免多 iframe 残留监听）。
 *
 * @param iframe iframe.contentWindow（postMessage 回传目标）
 * @param allowedOrigin 允许的 iframe source origin（'*' 仅用于同源自测，生产须具体 origin）
 */
export function installHostDeviceBridge(
  iframe: MessageSink,
  allowedOrigin: string,
  options: DeviceSimOptions = {},
) {
  const sim = createDeviceSim(options);
  const handler = createHostDeviceHandler(sim.device, iframe, allowedOrigin);

  /** window.message 监听：只处理来自 iframe 的消息，按 origin 白名单校验。 */
  function onMessage(event: MessageEvent) {
    if (allowedOrigin !== "*" && event.origin !== allowedOrigin) return;
    handler(event.data);
  }

  if (typeof window !== "undefined") {
    window.addEventListener("message", onMessage);
  }

  return {
    /** 调用记录（供 M08 沙箱证据 / 审计留证）。 */
    calls: sim.calls,
    /** 切换 Doze 态。 */
    setDoze: sim.setDoze,
    /** 复位（每个 run 开始前）。 */
    reset: sim.reset,
    /** 卸载监听（iframe 销毁时调用）。 */
    dispose: () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("message", onMessage);
      }
    },
  };
}
