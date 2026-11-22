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
    logs: 'runner fatal',
    snapshot: '',
  }));
});
