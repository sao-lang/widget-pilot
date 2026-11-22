'use strict';

// M08 一次性沙箱内 runner：esbuild 编译 TSX -> jsdom 挂载渲染 -> 结构化断言。
// 无网络、无系统 API、只读根文件系统；所有诊断经 stdout JSON 回控制面。
const fs = require('fs');
const path = require('path');
const esbuild = require('esbuild');
const { JSDOM, VirtualConsole } = require('jsdom');

const APP_DIR = '/app';
const INTERACTION_SELECTORS = {
  calendar_next_month: '[data-action="next-month"]',
  clock_tick: '[data-action="tick"]',
  alarm_toggle: '[data-action="alarm-toggle"]',
  task_check: '[data-action="task-check"]',
};

function readPayload() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => {
      try {
        resolve(JSON.parse(data));
      } catch (error) {
        reject(error);
      }
    });
    process.stdin.on('error', reject);
  });
}

async function compileTsx(source) {
  fs.writeFileSync('/tmp/widget.tsx', source, 'utf8');
  const entry = [
    "import React from 'react';",
    "import { createRoot } from 'react-dom/client';",
    "import Widget from './widget.tsx';",
    "const host = document.getElementById('root');",
    "createRoot(host).render(React.createElement(Widget, {",
    "  grid: '2x2', theme: 'default', locale: 'zh', tick: 0, data: {}",
    "}));",
  ].join('\n');
  fs.writeFileSync('/tmp/entry.jsx', entry, 'utf8');
  const built = await esbuild.build({
    absWorkingDir: '/tmp',
    entryPoints: ['/tmp/entry.jsx'],
    bundle: true,
    jsx: 'automatic',
    platform: 'browser',
    format: 'iife',
    write: false,
    nodePaths: [path.join(APP_DIR, 'node_modules')],
    define: { 'process.env.NODE_ENV': '"development"' },
    logLevel: 'silent',
  });
  return built.outputFiles[0].text;
}

// ---------------------------------------------------------------------------
// ADR-010 §2.2/§2.3：沙箱侧设备 API mock 与调用序列上报。
//
// 约束常量的单一事实来源是 packages/contracts/device-apis.json。沙箱镜像为
// 只读根且只 COPY runner.js，容器内读不到契约文件，因此内嵌一份同值副本作为
// 兜底；在本地（能读到契约文件）时优先加载真实契约，保证两种运行环境语义一致。
// 本 mock 不接真实设备系统 API，只做确定性压制建模。
// ---------------------------------------------------------------------------
const DEVICE_CONTRACT_FALLBACK = {
  limits: {
    'device.getLocation': { min_interval_ms: 30000, doze_deferred: true },
    'device.getBattery': { min_interval_ms: 60000, doze_deferred: false },
    'device.getNetworkStatus': { min_interval_ms: 15000, doze_deferred: true },
    'device.sendNotification': { max_per_hour: 5, doze_deferred: false },
    'device.scheduleRefresh': { min_interval_ms: 1800000, doze_deferred: false },
    'device.storage.write': { max_per_second: 10, doze_deferred: false },
  },
  thresholds: {
    full_render_per_minute: 10,
    retry_window_calls: 3,
    anr_task_ms: 10000,
    bitmap_memory_factor: 6,
  },
};

const DEVICE_FAKE_VALUES = {
  location: { latitude: 39.9042, longitude: 116.4074 },
  battery: 80,
  network: { connected: true, type: 'wifi' },
};

function loadDeviceContract(payloadContract) {
  if (payloadContract && payloadContract.api_limits) {
    return {
      limits: payloadContract.api_limits,
      thresholds: Object.assign({}, DEVICE_CONTRACT_FALLBACK.thresholds, payloadContract.thresholds || {}),
    };
  }
  const candidates = [
    path.join(__dirname, '../../packages/contracts/device-apis.json'),
    path.join(APP_DIR, 'device-apis.json'),
  ];
  for (const file of candidates) {
    try {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (parsed && parsed.api_limits && typeof parsed.api_limits === 'object') {
        return {
          limits: parsed.api_limits,
          thresholds: Object.assign({}, DEVICE_CONTRACT_FALLBACK.thresholds, parsed.thresholds || {}),
        };
      }
    } catch (_) {
      // 容器内无契约文件：使用内嵌同值副本。
    }
  }
  return DEVICE_CONTRACT_FALLBACK;
}

function safeKey(value) {
  return typeof value === 'string' ? value : undefined;
}

function installDeviceMock(window, payload, calls) {
  const contract = loadDeviceContract(payload.device_contract);
  const limits = contract.limits;
  const mountedAt = Date.now();
  const lastAt = Object.create(null);
  const hourEvents = [];
  const secondEvents = [];
  // Doze 态由 payload.device_env.doze 注入（默认 false）。
  const payloadEnv = (payload && payload.device_env) || {};
  let doze = Boolean(payloadEnv.doze);

  // 统一包装：每次调用都追加一条 {api,t_ms,code,args?,duration_ms}。
  function makeApi(apiName, handler) {
    return function (...fnArgs) {
      const startedAt = Date.now();
      const outcome = handler(fnArgs) || {};
      const code = outcome.code || 'OK';
      const entry = {
        api: apiName,
        t_ms: Date.now() - mountedAt,
        code,
        duration_ms: outcome.durationMs !== undefined
          ? outcome.durationMs
          : Math.max(0, Date.now() - startedAt),
      };
      if (outcome.args && Object.keys(outcome.args).length > 0) entry.args = outcome.args;
      calls.push(entry);
      const result = { ok: outcome.ok !== false, code };
      if (outcome.value !== undefined) result.value = outcome.value;
      return result;
    };
  }

  function dozeGuard(apiName) {
    const spec = limits[apiName] || {};
    return doze && spec.doze_deferred ? { code: 'DOZE_DEFERRED', ok: false } : null;
  }

  // min_interval_ms 类：两次成功调用的最小间隔。
  function intervalLimited(apiName, value) {
    return () => {
      const blocked = dozeGuard(apiName);
      if (blocked) return blocked;
      const min = Number((limits[apiName] || {}).min_interval_ms) || 0;
      const nowMs = Date.now();
      const last = lastAt[apiName];
      if (last !== undefined && nowMs - last < min) {
        return { code: 'RATE_LIMITED', ok: false };
      }
      lastAt[apiName] = nowMs;
      return { code: 'OK', value };
    };
  }

  // max_per_hour 类：滑动 1 小时窗口。
  function hourlyLimited(apiName, value) {
    return () => {
      const blocked = dozeGuard(apiName);
      if (blocked) return blocked;
      const max = Number((limits[apiName] || {}).max_per_hour) || 0;
      const nowMs = Date.now();
      for (let i = hourEvents.length - 1; i >= 0; i -= 1) {
        if (nowMs - hourEvents[i] >= 3600000) hourEvents.splice(i, 1);
      }
      if (hourEvents.length >= max) return { code: 'QUOTA_EXCEEDED', ok: false };
      hourEvents.push(nowMs);
      return { code: 'OK', value };
    };
  }

  // max_per_second 类：滑动 1 秒窗口；只记 key，不记写入值（敏感值）。
  function perSecondLimited(apiName, value) {
    return (args) => {
      const blocked = dozeGuard(apiName);
      if (blocked) return blocked;
      const max = Number((limits[apiName] || {}).max_per_second) || 0;
      const nowMs = Date.now();
      for (let i = secondEvents.length - 1; i >= 0; i -= 1) {
        if (nowMs - secondEvents[i] >= 1000) secondEvents.splice(i, 1);
      }
      if (secondEvents.length >= max) return { code: 'RATE_LIMITED', ok: false };
      secondEvents.push(nowMs);
      const key = safeKey(args[0]);
      return { code: 'OK', value, args: key === undefined ? undefined : { key } };
    };
  }

  const device = {
    getLocation: makeApi('device.getLocation', intervalLimited('device.getLocation', DEVICE_FAKE_VALUES.location)),
    getBattery: makeApi('device.getBattery', intervalLimited('device.getBattery', DEVICE_FAKE_VALUES.battery)),
    getNetworkStatus: makeApi('device.getNetworkStatus', intervalLimited('device.getNetworkStatus', DEVICE_FAKE_VALUES.network)),
    sendNotification: makeApi('device.sendNotification', hourlyLimited('device.sendNotification', { sent: true })),
    scheduleRefresh: makeApi('device.scheduleRefresh', (args) => {
      const blocked = dozeGuard('device.scheduleRefresh');
      if (blocked) return blocked;
      const ms = Number(args[0]);
      const min = Number((limits['device.scheduleRefresh'] || {}).min_interval_ms) || 0;
      if (!Number.isFinite(ms) || ms < min) return { code: 'DENIED', ok: false, args: { ms } };
      return { code: 'OK', value: { scheduled: true }, args: { ms } };
    }),
    storage: {
      write: makeApi('device.storage.write', perSecondLimited('device.storage.write', true)),
    },
    render: {
      // 全量/局部渲染只按 mode 记录，真实渲染由 jsdom 侧完成。
      update: makeApi('device.render.update', (args) => {
        const mode = args[0] === undefined ? 'full' : String(args[0]);
        return { code: 'OK', value: { updated: true }, args: { mode } };
      }),
    },
    // 不真正执行长任务（避免拖慢沙箱）：传数字则只记录 duration_ms；
    // 传函数则执行它（短任务）并保留其返回值，长耗时由 durationMs 声明。
    compute: makeApi('device.compute', (args) => {
      const first = args[0];
      const second = args[1];
      let durationMs;
      let value;
      if (typeof first === 'number') durationMs = first;
      else if (typeof second === 'number') durationMs = second;
      const fn = typeof first === 'function' ? first : (typeof second === 'function' ? second : null);
      if (fn) {
        try {
          value = fn();
        } catch (_) {
          return { code: 'DENIED', ok: false };
        }
      }
      return { code: 'OK', value, durationMs };
    }),
  };
  // 兼容 window.widgetDevice.device.* 与 window.widgetDevice.* 两种写法。
  device.device = device;

  window.__deviceCalls = calls;
  window.widgetDevice = device;
}

async function renderAndAssert(compiled, payload, logs) {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (error) => logs.push('jsdom: ' + error.message));
  virtualConsole.on('error', (...parts) => logs.push('console.error: ' + parts.join(' ')));

  const dom = new JSDOM(
    '<!doctype html><html><body><div id="root"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole },
  );
  const { window } = dom;
  global.window = window;
  global.document = window.document;
  // Node 22 的 global.navigator 是只读 getter，必须用 defineProperty 覆盖。
  Object.defineProperty(global, 'navigator', {
    value: window.navigator,
    configurable: true,
  });
  global.HTMLElement = window.HTMLElement;
  global.Element = window.Element;
  global.Node = window.Node;
  // 沙箱不是 React 测试环境：关闭 act 警告，避免把测试框架提示误判为运行时错误。
  window.IS_REACT_ACT_ENVIRONMENT = false;

  const errors = [];
  window.addEventListener('error', (event) => errors.push(String(event.message)));
  const events = [];
  const declared = new Set(
    (payload.manifest && payload.manifest.capabilities) || [],
  );
  window.__bridgeEvents = events;
  window.bridge = {
    call(capability) {
      const allowed = declared.has(capability);
      events.push({ type: 'capability_request', capability, allowed });
      if (!allowed) {
        events.push({ type: 'widget:error', code: 'CAPABILITY_DENIED', capability });
        throw new Error('CAPABILITY_DENIED: ' + capability);
      }
      return undefined;
    },
  };

  // ADR-010 §2.3：设备 API mock + 调用序列（与 bridge 能力记录互不干扰）。
  const deviceCalls = [];
  installDeviceMock(window, payload, deviceCalls);

  window.eval(compiled);
  await new Promise((resolve) => setTimeout(resolve, 30));

  const grid = String((payload.manifest && payload.manifest.grid) || '2x2');
  const parts = grid.split('x');
  const widthPx = Number(parts[0]) * 80;
  const heightPx = Number(parts[1]) * 80;
  const host = window.document.getElementById('root');
  host.style.width = widthPx + 'px';
  host.style.height = heightPx + 'px';

  let maxChildWidth = 0;
  let maxChildHeight = 0;
  window.document.querySelectorAll('*').forEach((element) => {
    const w = parseInt(element.style.width, 10);
    const h = parseInt(element.style.height, 10);
    if (Number.isFinite(w)) maxChildWidth = Math.max(maxChildWidth, w);
    if (Number.isFinite(h)) maxChildHeight = Math.max(maxChildHeight, h);
  });

  const interactions = Object.keys(INTERACTION_SELECTORS).map((name) => {
    const element = window.document.querySelector(INTERACTION_SELECTORS[name]);
    if (!element) return { name, passed: true, applicable: false };
    const before = element.textContent;
    try {
      element.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    } catch (error) {
      errors.push(name + ': ' + error.message);
    }
    const after = element.textContent;
    const changed = before !== after || element.getAttribute('data-changed') !== null;
    return { name, passed: changed, applicable: true };
  });

  return {
    render: {
      grid,
      width_px: widthPx,
      height_px: heightPx,
      max_child_width_px: maxChildWidth,
      max_child_height_px: maxChildHeight,
      errors,
    },
    bridge: { events },
    interactions,
    device_calls: deviceCalls.slice(),
    snapshot: dom.serialize(),
  };
}

async function main() {
  const payload = await readPayload();
  const logs = [];
  let compiled = '';
  let renderResult = null;
  try {
    compiled = await compileTsx(String(payload.source || ''));
    renderResult = await renderAndAssert(compiled, payload, logs);
  } catch (error) {
    logs.push('sandbox: ' + (error && error.message ? error.message : String(error)));
  }
  const grid = String((payload.manifest && payload.manifest.grid) || '2x2');
  const render = renderResult && renderResult.render
    ? renderResult.render
    : {
        grid,
        width_px: -1,
        height_px: -1,
        max_child_width_px: -1,
        max_child_height_px: -1,
        errors: logs.slice(),
      };
  const report = {
    stages: {
      compile: { compiled },
      render,
      bridge: renderResult ? renderResult.bridge : { events: [] },
    },
    interactions: renderResult ? renderResult.interactions : [],
    device_calls: renderResult ? renderResult.device_calls : [],
    logs: logs.join('\n'),
    snapshot: renderResult ? renderResult.snapshot : '',
  };
  process.stdout.write(JSON.stringify(report));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    stages: {
      compile: { compiled: '' },
      render: {
        grid: '2x2', width_px: -1, height_px: -1,
        max_child_width_px: -1, max_child_height_px: -1,
        errors: ['runner fatal: ' + String(error && error.message ? error.message : error)],
      },
      bridge: { events: [] },
    },
    interactions: [],
    device_calls: [],
    logs: 'runner fatal',
    snapshot: '',
  }));
});
