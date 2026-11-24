/**
 * ADR-010 §2 设备 API 浏览器模拟运行时（window.widgetDevice）。
 *
 * 约束值单一事实来源：packages/contracts/device-apis.json（与 Python
 * services/orchestrator/device_sim/detector.py 读同一份，语义一致）。
 * 只做确定性压制判定，不接真实设备系统 API（ADR-010 §2.1 铁律）。
 *
 * 设计纪律（ADR-010 §3.1）：白名单 + 限流是确定性门禁，不调模型、不做语义放行；
 * 未声明/动态构造的 API 一律 DENIED 并记 dangerous_api 调用，供沙箱证据上报。
 */
// 单一事实来源：运行时与 Python detector 读同一份 device-apis.json。
// import attributes 让 Node 22 strip-types 与 TS resolveJsonModule 都能加载 JSON。
import contract from "../../contracts/device-apis.json" with { type: "json" };

export type ResultCode = "OK" | "RATE_LIMITED" | "DOZE_DEFERRED" | "QUOTA_EXCEEDED" | "DENIED";

/** 一次设备 API 调用记录（对应 Python DeviceCall，不记敏感 value）。 */
export interface DeviceCallRecord {
  api: string;
  t_ms: number;
  code: ResultCode;
  duration_ms: number;
}

/** 单次 API 调用结果（ADR-010 §2.2：{ok, value?, code?}）。 */
export interface DeviceResult<T = unknown> {
  ok: boolean;
  value?: T;
  code?: ResultCode;
}

/** 可注入时钟（测试确定性；生产用 Date.now）。 */
export type Clock = () => number;

export interface DeviceSimOptions {
  /** 注入时钟，默认 Date.now。 */
  clock?: Clock;
  /** 初始 Doze 态（默认 false，可运行时 setDoze 切换）。 */
  doze?: boolean;
}

interface ApiLimit {
  min_interval_ms?: number;
  max_per_hour?: number;
  max_per_second?: number;
  doze_deferred?: boolean;
}

/** 契约的 api_limits 视图（JSON 导入的结构化收窄）。 */
const API_LIMITS = contract.api_limits as Record<string, ApiLimit>;
const THRESHOLDS = contract.thresholds as Record<string, number>;
const RESULT_CODES = new Set<string>(contract.result_codes);

/** 白名单 = api_limits 的键 + render.update/compute（与 Python detector 一致）。 */
const ALLOWED_APIS = new Set<string>([
  ...Object.keys(API_LIMITS),
  "device.render.update",
  "device.compute",
]);

/** 判定某 API 是否受 Doze 延迟（默认 false：未列 doze_deferred 的不延迟）。 */
function dozeDeferred(api: string): boolean {
  return API_LIMITS[api]?.doze_deferred === true;
}

/**
 * 创建设备模拟运行时（返回挂到 window.widgetDevice 的对象 + 调用记录）。
 *
 * @param options 可注入时钟与初始 Doze 态
 */
export function createDeviceSim(options: DeviceSimOptions = {}) {
  const now: Clock = options.clock ?? (() => Date.now());
  let doze = options.doze ?? false;
  const calls: DeviceCallRecord[] = [];
  const lastCallAt = new Map<string, number>();
  const hourWindow = new Map<string, number[]>();
  const secondWindow = new Map<string, number[]>();

  /** 记录一次调用（不记 value，避免敏感值入证据）。 */
  function record(api: string, t: number, code: ResultCode, duration = 0): void {
    calls.push({ api, t_ms: t, code, duration_ms: duration });
  }

  /** 通用压制判定：白名单 → Doze → 最小间隔 → 小时配额 → 秒配额。 */
  function gate(api: string): ResultCode | null {
    if (!ALLOWED_APIS.has(api)) return "DENIED";
    if (doze && dozeDeferred(api)) return "DOZE_DEFERRED";

    const limit = API_LIMITS[api];
    if (!limit) return null; // render.update / compute 白名单内、无频率约束
    const t = now();

    if (limit.min_interval_ms) {
      const last = lastCallAt.get(api);
      if (last !== undefined && t - last < limit.min_interval_ms) return "RATE_LIMITED";
    }
    if (limit.max_per_hour) {
      const hits = (hourWindow.get(api) ?? []).filter((x) => t - x < 3_600_000);
      if (hits.length >= limit.max_per_hour) return "QUOTA_EXCEEDED";
      hourWindow.set(api, [...hits, t]);
    }
    if (limit.max_per_second) {
      const hits = (secondWindow.get(api) ?? []).filter((x) => t - x < 1000);
      if (hits.length >= limit.max_per_second) return "RATE_LIMITED";
      secondWindow.set(api, [...hits, t]);
    }
    if (limit.min_interval_ms) lastCallAt.set(api, t);
    return null;
  }

  /** 执行一次受约束 API 调用，返回结构化结果并留证。 */
  function invoke<T>(api: string, value: () => T): DeviceResult<T> {
    const t = now();
    const deny = gate(api);
    if (deny !== null) {
      record(api, t, deny);
      return { ok: false, code: deny };
    }
    const started = now();
    const result = value();
    const duration = Math.max(0, now() - started);
    record(api, t, "OK", duration);
    return { ok: true, value: result, code: "OK" };
  }

  /** scheduleRefresh 特殊约束：ms < 30min 直接拒绝（ADR-010 §2.2）。 */
  function scheduleRefresh(ms: number): DeviceResult<null> {
    const t = now();
    const minimum = API_LIMITS["device.scheduleRefresh"]?.min_interval_ms ?? 1_800_000;
    if (ms < minimum) {
      record("device.scheduleRefresh", t, "RATE_LIMITED");
      return { ok: false, code: "RATE_LIMITED" };
    }
    return invoke("device.scheduleRefresh", () => null);
  }

  /** 全量渲染计数（render_storm 阈值来自契约，超限降级但记录）。 */
  function renderUpdate(mode: "full" | "partial"): DeviceResult<null> {
    const t = now();
    const withinMinute = calls.filter(
      (c) => c.api === "device.render.update" && t - c.t_ms < 60_000,
    ).length;
    const threshold = THRESHOLDS.full_render_per_minute ?? 10;
    const code: ResultCode = mode === "full" && withinMinute >= threshold ? "RATE_LIMITED" : "OK";
    record("device.render.update", t, code);
    return { ok: code === "OK", code };
  }

  const device = {
    getLocation: () => invoke("device.getLocation", () => ({ lat: 0, lng: 0 })),
    getBattery: () => invoke("device.getBattery", () => ({ level: 1, charging: false })),
    getNetworkStatus: () => invoke("device.getNetworkStatus", () => ({ online: true })),
    sendNotification: (message: string) =>
      invoke("device.sendNotification", () => ({ delivered: Boolean(message) })),
    scheduleRefresh,
    storage: {
      write: (key: string, value: string) =>
        invoke("device.storage.write", () => ({ key, bytes: value.length })),
      read: (key: string) => invoke("device.compute", () => ({ key, value: null })),
    },
    render: { update: renderUpdate },
    compute: <T>(fn: () => T) => invoke("device.compute", fn),
  };

  return {
    /** 挂到 window.widgetDevice 的对象。 */
    device,
    /** 调用记录（供沙箱证据上报；对应 Python DeviceCall 序列）。 */
    calls: () => [...calls],
    /** 切换 Doze 态（测试/宿主调度用）。 */
    setDoze: (value: boolean) => { doze = value; },
    /** 清空调用记录与窗口（每 run 复位）。 */
    reset: () => {
      calls.length = 0;
      lastCallAt.clear();
      hourWindow.clear();
      secondWindow.clear();
    },
    /** 契约阈值（供宿主/测试查询，单一事实来源）。 */
    limits: () => ({ api_limits: API_LIMITS, thresholds: THRESHOLDS }),
  };
}

/** 判定 code 是否为契约定义的结果码（fail-closed，未知码拒绝）。 */
export function isKnownResultCode(code: string): code is ResultCode {
  return RESULT_CODES.has(code);
}

/** 把模拟运行时挂到 window.widgetDevice（浏览器侧入口；无 window 时 no-op）。 */
export function installDeviceSim(options: DeviceSimOptions = {}) {
  const sim = createDeviceSim(options);
  if (typeof window !== "undefined") {
    (window as unknown as { widgetDevice: typeof sim.device }).widgetDevice = sim.device;
  }
  return sim;
}
