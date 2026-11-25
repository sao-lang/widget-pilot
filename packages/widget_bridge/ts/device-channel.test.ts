/** ADR-010 设备 bridge 通道单测（node:test，注入 sink，零浏览器依赖）。 */
import assert from "node:assert/strict";
import test from "node:test";

import { createDeviceSim } from "../../device_sim/ts/index.ts";
import {
  DEVICE_CHANNEL,
  createDeviceProxy,
  createHostDeviceHandler,
  createWidgetDeviceProxy,
  installHostDeviceBridge,
  invokeDeviceApi,
  isDeviceCall,
  isDeviceResult,
  isResultCode,
  type MessageSink,
} from "./device-channel.ts";

/** 记录 postMessage 的 sink。 */
function recordingSink() {
  const sent: unknown[] = [];
  const sink: MessageSink = {
    postMessage(message) { sent.push(message); },
  };
  return { sink, sent };
}

test("createDeviceProxy 发起调用 → postMessage 带 requestId/api/args", async () => {
  const { sink, sent } = recordingSink();
  let id = 0;
  const proxy = createDeviceProxy(sink, "https://host", () => {}, () => `r${++id}`);
  const promise = proxy.call("device.getBattery", []);
  assert.equal(sent.length, 1);
  const msg = sent[0] as Record<string, unknown>;
  assert.equal(msg.kind, DEVICE_CHANNEL.CALL);
  assert.equal(msg.requestId, "r1");
  assert.equal(msg.api, "device.getBattery");
  assert.equal(proxy.pendingCount(), 1);
  // 宿主回传
  const result = { ok: true, value: { level: 1 }, code: "OK" } as const;
  assert.equal(proxy.receive({ kind: DEVICE_CHANNEL.RESULT, requestId: "r1", result }), true);
  assert.equal(proxy.pendingCount(), 0);
  assert.deepEqual(await promise, result);
});

test("未知 requestId 的回包被忽略（防伪造/重复）", () => {
  const { sink } = recordingSink();
  const proxy = createDeviceProxy(sink, "*", () => {});
  const accepted = proxy.receive({
    kind: DEVICE_CHANNEL.RESULT,
    requestId: "not-pending",
    result: { ok: true },
  });
  assert.equal(accepted, false);
});

test("isDeviceCall / isDeviceResult fail-closed（字段不全拒绝）", () => {
  assert.equal(isDeviceCall({ kind: "device:call", requestId: "r", api: "x", args: [] }), true);
  assert.equal(isDeviceCall({ kind: "device:call", requestId: "r", api: "x" }), false);
  assert.equal(isDeviceCall(null), false);
  assert.equal(isDeviceResult({ kind: "device:result", requestId: "r", result: {} }), true);
  assert.equal(isDeviceResult({ kind: "device:result", requestId: "r" }), false);
});

test("isResultCode 与契约 result_codes 一致（未知码拒绝）", () => {
  for (const code of ["OK", "RATE_LIMITED", "DOZE_DEFERRED", "QUOTA_EXCEEDED", "DENIED"]) {
    assert.equal(isResultCode(code), true);
  }
  assert.equal(isResultCode("UNKNOWN"), false);
});

// ---- 端到端：iframe widgetDevice 方法 → postMessage → 宿主 sim → 回传 ----

/** 两个 sink 互联：iframe 发的消息直接投递给宿主 handler，宿主回投递给 iframe。 */
function pairedChannel(simDevice: Parameters<typeof createHostDeviceHandler>[0]) {
  const iframeQueue: unknown[] = [];
  let hostHandle: (v: unknown) => boolean = () => false;
  const iframeSink: MessageSink = { postMessage(m) { hostHandle(m); } };
  const hostSink: MessageSink = { postMessage(m) { iframeQueue.push(m); } };
  hostHandle = createHostDeviceHandler(simDevice, hostSink, "*");
  return { iframeQueue, iframeSink, hostSink, hostHandle };
}

test("端到端：widgetDevice.getBattery() 经 bridge 拿到 sim 结果", async () => {
  let id = 0;
  const nextId = () => `r${++id}`;
  const sim = createDeviceSim({ clock: () => 1000 });
  const ch = pairedChannel(sim.device);

  const results: unknown[] = [];
  const proxy = createDeviceProxy(ch.iframeSink, "*", (_rid, r) => results.push(r), nextId);
  const widgetDevice = createWidgetDeviceProxy(proxy);

  const promise = widgetDevice.getBattery(); // widget 源码写法不变
  // 宿主已同步回传，把队列里的 device:result 喂给 iframe 侧 receive
  for (const msg of ch.iframeQueue) proxy.receive(msg);
  const result = await promise;
  assert.equal(result.ok, true);
  assert.deepEqual(result.value, { level: 1, charging: false });
  assert.equal(results.length, 1);
});

test("端到端：宿主限流通过 bridge 回传 RATE_LIMITED", async () => {
  let id = 0;
  const nextId = () => `r${++id}`;
  const sim = createDeviceSim({ clock: () => 5000 });
  const ch = pairedChannel(sim.device);
  const results: unknown[] = [];
  const proxy = createDeviceProxy(ch.iframeSink, "*", (_rid, r) => results.push(r), nextId);
  const widgetDevice = createWidgetDeviceProxy(proxy);

  const first = widgetDevice.getLocation();
  for (const m of ch.iframeQueue.splice(0)) proxy.receive(m);
  assert.equal((await first).ok, true);

  const second = widgetDevice.getLocation(); // 同 t，< 30s 最小间隔
  for (const m of ch.iframeQueue.splice(0)) proxy.receive(m);
  const secondResult = await second;
  assert.equal(secondResult.ok, false);
  assert.equal(secondResult.code, "RATE_LIMITED");
});

test("端到端：Doze 态下 getLocation 经 bridge 回传 DOZE_DEFERRED", async () => {
  let id = 0;
  const sim = createDeviceSim({ clock: () => 0, doze: true });
  const ch = pairedChannel(sim.device);
  const proxy = createDeviceProxy(ch.iframeSink, "*", () => {}, () => `r${++id}`);
  const widgetDevice = createWidgetDeviceProxy(proxy);
  const promise = widgetDevice.getLocation();
  for (const m of ch.iframeQueue.splice(0)) proxy.receive(m);
  assert.equal((await promise).code, "DOZE_DEFERRED");
});

test("invokeDeviceApi 未知 API fail-closed DENIED（不调运行时）", () => {
  const sim = createDeviceSim({ clock: () => 0 });
  const result = invokeDeviceApi(sim.device, "device.__proto__.constructor", []);
  assert.equal(result.ok, false);
  assert.equal(result.code, "DENIED");
});

test("宿主 handler 只消费 device:call，忽略其他消息", () => {
  const sim = createDeviceSim({ clock: () => 0 });
  const ch = pairedChannel(sim.device);
  assert.equal(ch.hostHandle({ kind: "widget:ready" }), false);
  assert.equal(ch.hostHandle({ kind: "device:result", requestId: "x", result: {} }), false);
  assert.equal(ch.hostHandle({ kind: "device:call", requestId: "r1", api: "device.getBattery", args: [] }), true);
  assert.equal(ch.iframeQueue.length, 1);
});

test("installHostDeviceBridge 装配后监听消息、回传结果、可 dispose", () => {
  // 注入可控的 window（测试环境无真 DOM）
  const listeners = new Set<(e: { origin: string; data: unknown }) => void>();
  const fakeWindow = {
    addEventListener: (_type: string, fn: (e: { origin: string; data: unknown }) => void) => listeners.add(fn),
    removeEventListener: (_type: string, fn: (e: { origin: string; data: unknown }) => void) => listeners.delete(fn),
  };
  const originalWindow = (globalThis as Record<string, unknown>).window;
  (globalThis as Record<string, unknown>).window = fakeWindow;

  try {
    const posted: unknown[] = [];
    const iframe: MessageSink = { postMessage(m) { posted.push(m); } };
    const handle = installHostDeviceBridge(iframe, "https://widget.example", { clock: () => 100 });

    // 伪造一条来自正确 origin 的 device:call
    for (const fn of listeners) fn({ origin: "https://widget.example", data: { kind: "device:call", requestId: "r1", api: "device.getBattery", args: [] } });
    assert.equal(posted.length, 1);
    const reply = posted[0] as Record<string, unknown>;
    assert.equal(reply.kind, "device:result");
    assert.equal(reply.requestId, "r1");
    assert.equal(handle.calls().length, 1);

    // 错误 origin 被忽略
    for (const fn of listeners) fn({ origin: "https://evil.example", data: { kind: "device:call", requestId: "r2", api: "device.getBattery", args: [] } });
    assert.equal(posted.length, 1);

    handle.dispose();
    assert.equal(listeners.size, 0);
  } finally {
    (globalThis as Record<string, unknown>).window = originalWindow;
  }
});
