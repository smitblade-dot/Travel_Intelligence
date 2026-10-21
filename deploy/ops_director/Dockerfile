# 03AA-04_Master_Agent_TI_Ops_Director: Travel Intelligence controller (Python standard library only).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=UTC \
    TI_OPS_DB_PATH=/data/ops_director.sqlite3 TI_OPS_REPO_ROOT=/repo
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin opsdirector \
 && mkdir -p /data /app && chown opsdirector /data
WORKDIR /app
COPY scripts/ /app/scripts/
COPY config/ops_director.example.json config/ops_director_pricing.json config/production_controller.json /app/config/
# /repo is a read-only checkout of Travel_Intelligence (feed evidence and source config); see docs.
USER opsdirector
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "scripts/ops_director.py", "healthcheck"]
ENTRYPOINT ["python", "scripts/ops_director.py"]
CMD ["daemon", "--mode", "live"]
