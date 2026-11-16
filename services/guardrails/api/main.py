"""guardrails API 入口（:8100；护栏判据实现归 M06/M08）。"""
from services._health import create_health_app

app = create_health_app("guardrails", 8100)
