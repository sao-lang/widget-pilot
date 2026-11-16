"""retriever API 入口（:8200；检索实现归 M10）。"""
from services._health import create_health_app

app = create_health_app("retriever", 8200)
