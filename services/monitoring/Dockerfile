FROM python:3.13-slim
RUN useradd --uid 1001 --no-create-home --shell /usr/sbin/nologin probes
COPY services/monitoring/src/server_monitor/probes.py /probes.py
USER probes
CMD ["python", "/probes.py"]
