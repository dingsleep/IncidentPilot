FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.runtime.lock pyproject.toml ./
RUN pip install --no-cache-dir --no-deps -r requirements.runtime.lock
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/seed_local_data.py ./scripts/seed_local_data.py
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 incidentpilot

USER incidentpilot
EXPOSE 8200
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8200/api/v1/health/live', timeout=2)"
CMD ["python", "-m", "uvicorn", "incidentpilot.api.main:app", "--host", "0.0.0.0", "--port", "8200"]
