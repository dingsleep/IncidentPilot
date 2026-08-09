from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from incidentpilot.telemetry.backends.jaeger import JaegerBackend
from incidentpilot.telemetry.backends.opensearch import OpenSearchBackend
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import LogSearch, MetricQuery, TraceSearch

ROOT = Path(__file__).parents[2]


@pytest.mark.integration
async def test_real_otel_backends_return_metrics_logs_traces_and_dependencies() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(minutes=10)
    registry = QueryRegistry.from_files(
        metrics_path=ROOT / "query_templates" / "metrics.yaml",
        logs_path=ROOT / "query_templates" / "logs.yaml",
        allowed_services={"checkout"},
    )
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        metrics = await PrometheusBackend(client=client, registry=registry).query_range(
            MetricQuery(
                template_id="service_request_rate",
                service="checkout",
                start=start,
                end=end,
                step_seconds=15,
            )
        )
        logs = await OpenSearchBackend(client=client).search(
            LogSearch(
                services=["checkout"],
                start=start,
                end=end,
                query_terms=[],
                limit=5,
            )
        )
        trace_request = TraceSearch(
            services=["checkout"],
            start=start,
            end=end,
            limit=5,
        )
        jaeger = JaegerBackend(client=client)
        traces = await jaeger.search(trace_request)
        dependencies = await jaeger.get_service_dependencies(trace_request)
        trace = await jaeger.get(traces[0].trace_id)

    assert metrics.series
    assert logs.records or logs.total == 0
    assert traces
    assert trace.spans
    assert dependencies
