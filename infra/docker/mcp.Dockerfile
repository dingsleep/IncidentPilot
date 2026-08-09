FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.runtime.lock pyproject.toml ./
RUN pip install --no-cache-dir --no-deps -r requirements.runtime.lock
COPY src ./src
COPY query_templates ./query_templates
COPY runbooks ./runbooks
COPY service_catalog ./service_catalog
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 incidentpilot

USER incidentpilot
EXPOSE 8101 8102
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import incidentpilot.mcp_servers.telemetry.server"
CMD ["python", "-m", "incidentpilot.mcp_servers.telemetry.server", "--host", "0.0.0.0"]
