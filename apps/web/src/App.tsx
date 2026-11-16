import { useEffect, useState } from "react";

// 环境骨架页：验证 web → control-plane 链路（M01 D7 冒烟）
// 真实 UI（会话中心/生成页/控件墙）归 M13–M16
export default function App() {
  const [health, setHealth] = useState<string>("checking...");

  useEffect(() => {
    fetch("/api/bff/health")
      .then((r) => (r.ok ? "control-plane OK" : `HTTP ${r.status}`))
      .catch((e) => `unreachable: ${e}`)
      .then(setHealth);
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: 24 }}>
      <h1>WidgetPilot</h1>
      <p>环境骨架（M01/M02）已就绪；业务 UI 由 M13–M16 实现。</p>
      <p>
        control-plane /healthz: <code>{health}</code>
      </p>
    </main>
  );
}
