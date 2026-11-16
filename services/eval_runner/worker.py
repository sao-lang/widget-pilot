"""eval-runner 作业型进程壳（M01 §4.1：无常驻端口，command 由具体回放任务覆写）。

评测回放实现归 M10/M19；此壳以 job 循环保持进程存活，并每 10s 刷新心跳文件
供 HEALTHCHECK（mtime 新鲜度）探测。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

HEARTBEAT_INTERVAL_S = 10


async def main() -> None:
    hb_path = Path(os.getenv("HEARTBEAT_FILE", "/tmp/eval_runner.heartbeat"))
    print(f"[eval-runner] idle loop env={os.getenv('ENV', 'dev')} (health shell; 覆写 command 执行具体回放任务)", flush=True)
    while True:
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        hb_path.write_text(json.dumps({"ts": time.time(), "service": "eval_runner"}))
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
