FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.runtime.lock pyproject.toml ./
RUN pip install --no-cache-dir --no-deps -r requirements.runtime.lock
COPY src ./src
COPY scenarios ./scenarios
COPY service_catalog ./service_catalog
COPY scripts/run_demo_runner.py ./scripts/run_demo_runner.py
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 incidentpilot

USER incidentpilot
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8300/health/ready')"
CMD ["python", "scripts/run_demo_runner.py"]
