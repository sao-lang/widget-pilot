/** ADR-010 设备 bridge 通道单测（node:test，注入 sink，零浏览器依赖）。 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEVICE_CHANNEL,
  createDeviceProxy,
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
