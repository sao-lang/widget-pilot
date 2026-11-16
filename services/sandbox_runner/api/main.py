"""sandbox-runner API 入口（:8300；沙箱交互按 M08 受控出口设计，不挂 docker.sock）。"""
from services._health import create_health_app

app = create_health_app("sandbox-runner", 8300)
