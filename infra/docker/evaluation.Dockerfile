FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.runtime.lock pyproject.toml ./
RUN pip install --no-cache-dir --no-deps -r requirements.runtime.lock
COPY src ./src
COPY prompts ./prompts
COPY query_templates ./query_templates
COPY runbooks ./runbooks
COPY scenarios ./scenarios
COPY service_catalog ./service_catalog
COPY scripts/run_eval.py ./scripts/run_eval.py
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 incidentpilot

USER incidentpilot
ENTRYPOINT ["python", "scripts/run_eval.py"]
