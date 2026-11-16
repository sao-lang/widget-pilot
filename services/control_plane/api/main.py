"""control-plane API 入口（M01 §4.1 full 组 :8000；业务实现归 M04/M09/M11）。"""
from services._health import create_health_app

app = create_health_app("control-plane", 8000)
