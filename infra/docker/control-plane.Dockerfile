# M01 §3.3 L2 服务层：control-plane（FROM L1 python-base：依赖已在基座层，此层仅源码）
FROM widgetpilot/python-base:dev
COPY --chown=10001:10001 packages/ packages/
COPY --chown=10001:10001 services/ services/
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "services.control_plane.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
