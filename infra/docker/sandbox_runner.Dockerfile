# M01 §3.3 L2 服务层：sandbox-runner（:8300；运行时外壳，
# 沙箱镜像本体 wp-sandbox 在 infra/sandbox 归 M08——本镜像不定义沙箱安全参数）
FROM widgetpilot/python-base:dev
COPY --chown=10001:10001 packages/ packages/
COPY --chown=10001:10001 services/ services/
EXPOSE 8300
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8300/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "services.sandbox_runner.api.main:app", "--host", "0.0.0.0", "--port", "8300"]
