from __future__ import annotations

import math
from typing import Any, cast

import httpx

from incidentpilot.telemetry.backends.http import ReadOnlyJsonClient
from incidentpilot.telemetry.normalization import (
    TelemetryBackendError,
    canonical_digest,
    epoch_seconds_to_utc,
    normalize_service_name,
    normalize_status_code,
)
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import (
    MetricPoint,
    MetricQuery,
    MetricSeries,
    MetricSeriesSet,
)


class PrometheusBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        registry: QueryRegistry,
        base_url: str = "http://127.0.0.1:9090",
        max_response_bytes: int = 1_000_000,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self._http = ReadOnlyJsonClient(
            client=client,
            base_url=base_url,
            max_response_bytes=max_response_bytes,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        self._registry = registry

    async def query_range(self, request: MetricQuery) -> MetricSeriesSet:
        expression = self._registry.render_metric(request)
        raw: Any = await self._http.request_json(
            "GET",
            "/api/v1/query_range",
            params={
                "query": expression,
                "start": request.start.timestamp(),
                "end": request.end.timestamp(),
                "step": request.step_seconds,
            },
        )
        payload = self._payload(raw)
        data = payload.get("data")
        if payload.get("status") != "success" or not isinstance(data, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE",
                "Prometheus returned an invalid response envelope",
                retryable=False,
            )
        data_object = cast(dict[str, Any], data)
        result = data_object.get("result")
        if not isinstance(result, list):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE",
                "Prometheus returned an invalid result",
                retryable=False,
            )
        series = [self._series(item) for item in cast(list[Any], result)]
        return MetricSeriesSet(
            series=series,
            unit=self._registry.metric_unit(request.template_id),
            raw_digest_sha256=canonical_digest(payload),
        )

    @staticmethod
    def _payload(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE",
                "Prometheus response must be an object",
                retryable=False,
            )
        return cast(dict[str, Any], raw)

    @staticmethod
    def _series(raw: Any) -> MetricSeries:
        if not isinstance(raw, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "Prometheus series is invalid", retryable=False
            )
        item = cast(dict[str, Any], raw)
        raw_labels = item.get("metric", {})
        if not isinstance(raw_labels, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "Prometheus labels are invalid", retryable=False
            )
        label_object = cast(dict[Any, Any], raw_labels)
        labels = {
            str(key): normalize_service_name(str(value))
            if str(key) == "service_name"
            else normalize_status_code(value)
            if str(key) == "status_code"
            else str(value)
            for key, value in label_object.items()
        }
        raw_points = item.get("values")
        if raw_points is None and "value" in item:
            raw_points = [item["value"]]
        if not isinstance(raw_points, list):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "Prometheus points are invalid", retryable=False
            )
        points: list[MetricPoint] = []
        for point in cast(list[Any], raw_points):
            if not isinstance(point, list):
                raise TelemetryBackendError(
                    "UPSTREAM_UNAVAILABLE",
                    "Prometheus point is invalid",
                    retryable=False,
                )
            values = cast(list[Any], point)
            if len(values) != 2:
                raise TelemetryBackendError(
                    "UPSTREAM_UNAVAILABLE",
                    "Prometheus point is invalid",
                    retryable=False,
                )
            value = float(values[1])
            if math.isfinite(value):
                points.append(
                    MetricPoint(
                        timestamp=epoch_seconds_to_utc(values[0]),
                        value=value,
                    )
                )
        return MetricSeries(labels=labels, points=points)
