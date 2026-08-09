from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.normalization import TelemetryBackendError
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import MetricQuery

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)


def _registry() -> QueryRegistry:
    return QueryRegistry.from_files(
        metrics_path=ROOT / "query_templates" / "metrics.yaml",
        logs_path=ROOT / "query_templates" / "logs.yaml",
        allowed_services={"checkout"},
    )


def _request() -> MetricQuery:
    return MetricQuery(
        template_id="service_request_rate",
        service="checkout",
        start=NOW,
        end=NOW,
        step_seconds=15,
    )


@pytest.mark.asyncio
async def test_prometheus_normalizes_series_and_empty_results() -> None:
    responses: list[dict[str, Any]] = [
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"service_name": "Checkout"},
                        "values": [[1784191200, "2.5"]],
                    }
                ],
            },
        },
        {"status": "success", "data": {"resultType": "matrix", "result": []}},
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        backend = PrometheusBackend(client=client, registry=_registry())
        result = await backend.query_range(_request())
        empty = await backend.query_range(_request())

    assert result.series[0].labels["service_name"] == "checkout"
    assert result.series[0].points[0].value == 2.5
    assert result.raw_digest_sha256
    assert empty.series == []


@pytest.mark.asyncio
async def test_prometheus_drops_non_finite_samples_without_dropping_the_series() -> None:
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"service_name": "Checkout"},
                    "values": [
                        [1784191200, "NaN"],
                        [1784191215, "+Inf"],
                        [1784191230, "-Inf"],
                        [1784191245, "2.5"],
                    ],
                }
            ],
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        result = await PrometheusBackend(client=client, registry=_registry()).query_range(
            _request()
        )

    assert [point.value for point in result.series[0].points] == [2.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_code", "expected_attempts"),
    [
        ("4xx", "INVALID_ARGUMENT", 1),
        ("5xx", "UPSTREAM_UNAVAILABLE", 1),
        ("timeout", "UPSTREAM_TIMEOUT", 1),
        ("malformed", "UPSTREAM_UNAVAILABLE", 1),
        ("oversize", "RESULT_TOO_LARGE", 1),
        ("retry", None, 3),
    ],
)
async def test_prometheus_error_and_retry_paths(
    mode: str,
    expected_code: str | None,
    expected_attempts: int,
) -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if mode == "4xx":
            return httpx.Response(400, json={"error": "bad query"})
        if mode == "5xx":
            return httpx.Response(500, json={"error": "failed"})
        if mode == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if mode == "malformed":
            return httpx.Response(200, content=b"{")
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * 1001)
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        backend = PrometheusBackend(
            client=client,
            registry=_registry(),
            max_response_bytes=1000,
            retry_backoff_seconds=0,
        )
        if expected_code is None:
            assert (await backend.query_range(_request())).series == []
        else:
            with pytest.raises(TelemetryBackendError) as exc:
                await backend.query_range(_request())
            assert exc.value.code == expected_code
    assert attempts == expected_attempts
