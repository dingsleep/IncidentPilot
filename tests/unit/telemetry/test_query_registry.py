from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import LogSearch, MetricRenderRequest

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)


def _registry() -> QueryRegistry:
    return QueryRegistry.from_files(
        metrics_path=ROOT / "query_templates" / "metrics.yaml",
        logs_path=ROOT / "query_templates" / "logs.yaml",
        allowed_services={"checkout", "payment", "frontend"},
    )


def test_registry_loads_required_metrics_and_renders_only_validated_parameters() -> None:
    registry = _registry()
    assert {
        "service_request_rate",
        "service_error_ratio",
        "service_latency_p95",
        "process_cpu_usage",
        "process_memory_usage",
        "container_memory_usage",
        "dependency_error_ratio",
    } <= registry.metric_ids

    query = registry.render_metric(
        MetricRenderRequest(
            template_id="service_latency_p95",
            service="checkout",
            window="5m",
            duration="10m",
            percentile=0.95,
        )
    )
    assert 'service_name="checkout"' in query
    assert "[5m]" in query
    assert "0.95" in query

    container_query = registry.render_metric(
        MetricRenderRequest(
            template_id="container_memory_usage",
            service="checkout",
            duration="2m",
        )
    )
    assert 'container_name="checkout"' in container_query
    assert "[2m]" in container_query

    with pytest.raises(ValidationError):
        MetricRenderRequest(
            template_id="service_error_ratio",
            service='checkout"} or vector(1)',
            window="5m",
        )
    with pytest.raises(ValidationError):
        MetricRenderRequest(
            template_id="service_error_ratio",
            service="checkout",
            window="5m] or vector(1)",
        )
    with pytest.raises(ValueError, match="service is not registered"):
        registry.render_metric(
            MetricRenderRequest(
                template_id="service_error_ratio",
                service="unknown-service",
            )
        )


def test_log_search_rejects_scripts_wildcards_and_unbounded_inputs() -> None:
    valid = LogSearch(
        services=["checkout", "payment"],
        severities=["ERROR"],
        start=NOW,
        end=NOW,
        query_terms=["charge failed"],
        trace_id="a" * 32,
        limit=20,
    )
    assert valid.limit == 20

    with pytest.raises(ValidationError, match="wildcard"):
        LogSearch(
            services=["checkout"],
            start=NOW,
            end=NOW,
            query_terms=["*"],
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        LogSearch.model_validate(
            {
                "services": ["checkout"],
                "start": NOW,
                "end": NOW,
                "query_terms": ["error"],
                "script": "return true",
            }
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        LogSearch(
            services=["checkout"],
            start=datetime(2026, 7, 16, 13, 0),
            end=NOW,
            query_terms=["error"],
        )


def test_registry_rejects_duplicate_ids_and_unknown_template_fields(
    tmp_path: Path,
) -> None:
    metrics = tmp_path / "metrics.yaml"
    logs = tmp_path / "logs.yaml"
    metrics.write_text(
        """
schema_version: 1
templates:
  - id: duplicate
    expression: up{service_name="${service}"}
    unit: ratio
    parameters: [service]
  - id: duplicate
    expression: up{service_name="${service}"}
    unit: ratio
    parameters: [service]
""".strip(),
        encoding="utf-8",
    )
    logs.write_text(
        """
schema_version: 1
templates:
  - id: service_logs
    index: otel-v1-apm-log-*
    parameters: [services, severities, start, end, query_terms, trace_id, limit]
    max_results: 200
    script: forbidden
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate template id"):
        QueryRegistry.from_files(
            metrics_path=metrics,
            logs_path=ROOT / "query_templates" / "logs.yaml",
            allowed_services={"checkout"},
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        QueryRegistry.from_files(
            metrics_path=ROOT / "query_templates" / "metrics.yaml",
            logs_path=logs,
            allowed_services={"checkout"},
        )
