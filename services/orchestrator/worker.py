"""orchestrator worker 进程探针壳（M01 §3.3：无端口 worker 用进程级探针）。

真实编排回路（checkpoint/lease/事件流）归 M04/M07；此壳仅维持进程存活
供 HEALTHCHECK pgrep 探测，并写最近心跳文件兜底。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

HEARTBEAT_INTERVAL_S = 10


async def main() -> None:
    hb_path = Path(os.getenv("HEARTBEAT_FILE", "/tmp/orchestrator.heartbeat"))
    print(f"[orchestrator] worker started env={os.getenv('ENV', 'dev')} (health shell)", flush=True)
    while True:
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        hb_path.write_text(json.dumps({"ts": time.time(), "service": "orchestrator"}))
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
