FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.runtime.lock pyproject.toml ./
RUN pip install --no-cache-dir --no-deps -r requirements.runtime.lock
COPY src ./src
COPY prompts ./prompts
COPY query_templates ./query_templates
COPY runbooks ./runbooks
COPY service_catalog ./service_catalog
COPY scripts/run_read_only_worker.py ./scripts/run_read_only_worker.py
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 incidentpilot

USER incidentpilot
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import incidentpilot.worker.main"
CMD ["python", "scripts/run_read_only_worker.py"]
