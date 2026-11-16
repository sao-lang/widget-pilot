# M01 §3.3 L2 服务层：eval-runner（作业型，无常驻端口；command 由回放任务覆写；
# 进程探针用心跳文件 mtime——worker 每 10s 刷新）
FROM widgetpilot/python-base:dev
COPY --chown=10001:10001 packages/ packages/
COPY --chown=10001:10001 services/ services/
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,time,sys; p=os.getenv('HEARTBEAT_FILE','/tmp/eval_runner.heartbeat'); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<40 else 1)"
CMD ["python", "-m", "services.eval_runner.worker"]
