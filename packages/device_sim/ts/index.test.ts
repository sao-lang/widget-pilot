/** ADR-010 设备 API 模拟运行时单测（node:test，零新依赖，注入时钟确定性）。 */
import assert from "node:assert/strict";
import test from "node:test";

import { createDeviceSim, isKnownResultCode, type Clock } from "./index.ts";

/** 可控时钟：每次调用返回当前 t，可手动推进。 */
function fakeClock(start = 0): { now: Clock; advance: (ms: number) => void } {
  let t = start;
  return {
    now: () => t,
    advance: (ms: number) => { t += ms; },
  };
}

test("getLocation 首次 OK，最小间隔内二次 RATE_LIMITED", () => {
  const c = fakeClock();
  const sim = createDeviceSim({ clock: c.now });
  assert.equal(sim.device.getLocation().ok, true);
  c.advance(10_000); // < 30s
  const second = sim.device.getLocation();
  assert.equal(second.ok, false);
  assert.equal(second.code, "RATE_LIMITED");
  c.advance(25_000); // 累计 35s > 30s
  assert.equal(sim.device.getLocation().ok, true);
});

test("Doze 态下 doze_deferred API 返回 DOZE_DEFERRED，非延迟 API 仍 OK", () => {
  const c = fakeClock();
  const sim = createDeviceSim({ clock: c.now, doze: true });
  const loc = sim.device.getLocation(); // doze_deferred=true
  assert.equal(loc.code, "DOZE_DEFERRED");
  const bat = sim.device.getBattery(); // doze_deferred=false
  assert.equal(bat.ok, true);
});

test("sendNotification 每小时配额用尽返回 QUOTA_EXCEEDED", () => {
  const c = fakeClock();
  const sim = createDeviceSim({ clock: c.now });
  for (let i = 0; i < 5; i++) {
    assert.equal(sim.device.sendNotification("hi").ok, true);
  }
  const sixth = sim.device.sendNotification("hi");
  assert.equal(sixth.ok, false);
  assert.equal(sixth.code, "QUOTA_EXCEEDED");
  c.advance(3_600_001); // 滑出 1 小时窗口
  assert.equal(sim.device.sendNotification("hi").ok, true);
});

test("scheduleRefresh < 30min 直接 RATE_LIMITED", () => {
  const sim = createDeviceSim({ clock: fakeClock().now });
  assert.equal(sim.device.scheduleRefresh(60_000).code, "RATE_LIMITED");
  assert.equal(sim.device.scheduleRefresh(1_800_000).ok, true);
});

test("render.update 全量渲染超阈值降级（render_storm 压制）", () => {
  const c = fakeClock();
  const sim = createDeviceSim({ clock: c.now });
  for (let i = 0; i < 10; i++) {
    assert.equal(sim.device.render.update("full").ok, true);
  }
  const overflow = sim.device.render.update("full"); // 第 11 次
  assert.equal(overflow.ok, false);
  assert.equal(overflow.code, "RATE_LIMITED");
  assert.equal(sim.device.render.update("partial").ok, true); // 局部更新不受限
});

test("storage.write 秒级限流（max_per_second=10）", () => {
  const c = fakeClock();
  const sim = createDeviceSim({ clock: c.now });
  for (let i = 0; i < 10; i++) sim.device.storage.write(`k${i}`, "v");
  assert.equal(sim.device.storage.write("k11", "v").code, "RATE_LIMITED");
  c.advance(1001);
  assert.equal(sim.device.storage.write("k12", "v").ok, true);
});

test("调用记录不记敏感 value，只记 api/t/code/duration", () => {
  const sim = createDeviceSim({ clock: fakeClock().now });
  sim.device.sendNotification("SECRET_MESSAGE");
  const [record] = sim.calls();
  assert.equal(record.api, "device.sendNotification");
  assert.equal(record.code, "OK");
  assert.ok(!JSON.stringify(record).includes("SECRET_MESSAGE"));
});

test("setDoze / reset 生效，每 run 可复位", () => {
  const sim = createDeviceSim({ clock: fakeClock().now });
  sim.device.getLocation();
  assert.equal(sim.calls().length, 1);
  sim.reset();
  assert.equal(sim.calls().length, 0);
  sim.setDoze(true);
  assert.equal(sim.device.getLocation().code, "DOZE_DEFERRED");
});

test("isKnownResultCode fail-closed：未知码拒绝", () => {
  for (const code of ["OK", "RATE_LIMITED", "DOZE_DEFERRED", "QUOTA_EXCEEDED", "DENIED"]) {
    assert.equal(isKnownResultCode(code), true);
  }
  assert.equal(isKnownResultCode("UNKNOWN"), false);
});

test("约束值来自 device-apis.json 单一事实来源", () => {
  const sim = createDeviceSim({ clock: fakeClock().now });
  const { api_limits, thresholds } = sim.limits();
  assert.equal(api_limits["device.getLocation"]?.min_interval_ms, 30_000);
  assert.equal(api_limits["device.scheduleRefresh"]?.min_interval_ms, 1_800_000);
  assert.equal(thresholds.full_render_per_minute, 10);
});
