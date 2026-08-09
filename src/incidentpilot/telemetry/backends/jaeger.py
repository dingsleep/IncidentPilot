from __future__ import annotations

from collections import Counter
from typing import Any, Literal, cast

import httpx

from incidentpilot.telemetry.backends.http import ReadOnlyJsonClient
from incidentpilot.telemetry.normalization import (
    TelemetryBackendError,
    canonical_digest,
    epoch_microseconds_to_utc,
    normalize_service_name,
    normalize_status_code,
)
from incidentpilot.telemetry.schemas import (
    ServiceDependency,
    SpanDocument,
    TraceDocument,
    TraceErrorSpan,
    TraceFailureType,
    TraceObservation,
    TraceSearch,
    TraceSummary,
)

_SAFE_OBSERVATION_TAGS = frozenset(
    {
        "app.cache_hit",
        "app.filtered_products.count",
        "app.products.count",
        "app.recommendation.cache_enabled",
    }
)
_FAILURE_DESCRIPTION_PATTERNS = (
    ("name resolver", "name_resolution_error"),
    ("produced zero addresses", "name_resolution_error"),
    ("connection refused", "connection_refused"),
    ("deadline exceeded", "deadline_exceeded"),
    ("timed out", "deadline_exceeded"),
    ("connect to redis", "storage_connection_failure"),
)


class JaegerBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:16686",
        max_response_bytes: int = 5_000_000,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self._http = ReadOnlyJsonClient(
            client=client,
            base_url=base_url,
            max_response_bytes=max_response_bytes,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    async def search(self, request: TraceSearch) -> list[TraceSummary]:
        traces = await self._fetch_traces(request)
        summaries = [self._summary(trace) for trace in traces]
        if request.error_only:
            summaries = [summary for summary in summaries if summary.error]
        return summaries[: request.limit]

    async def get(self, trace_id: str) -> TraceDocument:
        if len(trace_id) != 32 or any(char not in "0123456789abcdef" for char in trace_id):
            raise ValueError("trace_id must be 32 lowercase hexadecimal characters")
        raw: Any = await self._http.request_json("GET", f"/jaeger/ui/api/traces/{trace_id}")
        traces = self._trace_list(raw)
        if not traces:
            raise TelemetryBackendError("NOT_FOUND", "trace was not found", retryable=False)
        trace = traces[0]
        return TraceDocument(
            summary=self._summary(trace),
            spans=self._span_documents(trace),
        )

    async def get_service_dependencies(self, request: TraceSearch) -> list[ServiceDependency]:
        traces = await self._fetch_traces(request)
        calls: Counter[tuple[str, str]] = Counter()
        errors: Counter[tuple[str, str]] = Counter()
        for trace in traces:
            spans = self._spans(trace)
            processes = self._processes(trace)
            by_id = {
                str(span.get("spanID")): span
                for span in spans
                if isinstance(span.get("spanID"), str)
            }
            for span in spans:
                child = self._span_service(span, processes)
                for reference in self._references(span):
                    parent_span = by_id.get(str(reference.get("spanID")))
                    if parent_span is None:
                        continue
                    parent = self._span_service(parent_span, processes)
                    if parent and child and parent != child:
                        edge = (parent, child)
                        calls[edge] += 1
                        if self._span_error(span):
                            errors[edge] += 1
        return [
            ServiceDependency(
                parent_service=parent,
                child_service=child,
                call_count=count,
                error_count=errors[(parent, child)],
            )
            for (parent, child), count in sorted(calls.items())
        ]

    async def _fetch_traces(self, request: TraceSearch) -> list[dict[str, Any]]:
        traces: dict[str, dict[str, Any]] = {}
        for service in request.services:
            params: dict[str, Any] = {
                "service": service,
                "limit": request.limit,
                "start": int(request.start.timestamp() * 1_000_000),
                "end": int(request.end.timestamp() * 1_000_000),
            }
            if request.min_duration_ms:
                params["minDuration"] = f"{request.min_duration_ms}ms"
            raw: Any = await self._http.request_json("GET", "/jaeger/ui/api/traces", params=params)
            for trace in self._trace_list(raw):
                if trimmed := self._trim_to_range(trace, request):
                    traces[str(trimmed.get("traceID"))] = trimmed
        return list(traces.values())

    @classmethod
    def _trim_to_range(cls, trace: dict[str, Any], request: TraceSearch) -> dict[str, Any] | None:
        start = int(request.start.timestamp() * 1_000_000)
        end = int(request.end.timestamp() * 1_000_000)
        spans = [span for span in cls._spans(trace) if start <= int(span["startTime"]) <= end]
        return {**trace, "spans": spans} if spans else None

    @staticmethod
    def _trace_list(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "Jaeger response is invalid", retryable=False
            )
        payload = cast(dict[str, Any], raw)
        data = payload.get("data")
        if not isinstance(data, list):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "Jaeger response is invalid", retryable=False
            )
        return [
            cast(dict[str, Any], trace)
            for trace in cast(list[Any], data)
            if isinstance(trace, dict)
        ]

    @classmethod
    def _summary(cls, trace: dict[str, Any]) -> TraceSummary:
        spans = cls._spans(trace)
        if not spans:
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "Jaeger trace has no spans", retryable=False
            )
        starts = [int(span["startTime"]) for span in spans]
        ends = [int(span["startTime"]) + int(span["duration"]) for span in spans]
        processes = cls._processes(trace)
        services = sorted(
            {service for span in spans if (service := cls._span_service(span, processes))}
        )
        error_spans = cls._error_spans(spans, processes)
        error = bool(error_spans)
        return TraceSummary(
            trace_id=str(trace["traceID"]).casefold(),
            services=services,
            started_at=epoch_microseconds_to_utc(min(starts)),
            duration_ms=(max(ends) - min(starts)) / 1000,
            error=error,
            error_services=sorted({span.service for span in error_spans}),
            error_spans=error_spans,
            observations=cls._observations(spans, processes),
            status_code="ERROR" if error else "OK",
            span_count=len(spans),
            raw_digest_sha256=canonical_digest(trace),
        )

    @classmethod
    def _error_spans(
        cls,
        spans: list[dict[str, Any]],
        processes: dict[str, dict[str, Any]],
    ) -> list[TraceErrorSpan]:
        details: dict[
            tuple[str, str, Literal["OK", "ERROR", "UNSET"]],
            TraceFailureType | None,
        ] = {}
        for span in spans:
            service = cls._span_service(span, processes)
            if not service or not cls._span_error(span):
                continue
            tags = {str(tag.get("key")): tag.get("value") for tag in cls._tags(span)}
            key = (service, str(span.get("operationName", "")), cls._span_status(tags))
            details[key] = cls._failure_type(tags) or details.get(key)
        return [
            TraceErrorSpan(
                service=service,
                operation=operation,
                status_code=status,
                failure_type=failure_type,
            )
            for (service, operation, status), failure_type in sorted(details.items())[:12]
        ]

    @staticmethod
    def _failure_type(
        tags: dict[str, Any],
    ) -> TraceFailureType | None:
        error_type = str(tags.get("error.type", "")).casefold()
        if error_type in {"ratelimiterror", "rate_limit_error", "rate_limit_exceeded"}:
            return "rate_limited"
        description = str(tags.get("otel.status_description", "")).casefold()
        for marker, failure_type in _FAILURE_DESCRIPTION_PATTERNS:
            if marker in description:
                return cast(TraceFailureType, failure_type)
        grpc_status = tags.get("rpc.grpc.status_code")
        if grpc_status in {3, "3"}:
            return "invalid_argument"
        if grpc_status in {4, "4"}:
            return "deadline_exceeded"
        if grpc_status in {5, "5"}:
            return "not_found"
        if grpc_status in {14, "14"}:
            return "unavailable"
        return None

    @classmethod
    def _observations(
        cls,
        spans: list[dict[str, Any]],
        processes: dict[str, dict[str, Any]],
    ) -> list[TraceObservation]:
        observations: list[TraceObservation] = []
        seen: set[str] = set()
        for span in spans:
            service = cls._span_service(span, processes)
            if not service:
                continue
            tags = {str(tag.get("key")): tag.get("value") for tag in cls._tags(span)}
            attributes = {
                key: value
                for key in sorted(_SAFE_OBSERVATION_TAGS)
                if isinstance((value := tags.get(key)), (bool, int, float, str))
                and (not isinstance(value, str) or len(value) <= 100)
            }
            if not attributes:
                continue
            observation = TraceObservation(
                service=service,
                operation=str(span.get("operationName", "")),
                attributes=attributes,
            )
            digest = canonical_digest(observation.model_dump(mode="json"))
            if digest not in seen:
                seen.add(digest)
                observations.append(observation)
        return observations[:12]

    @classmethod
    def _span_documents(cls, trace: dict[str, Any]) -> list[SpanDocument]:
        processes = cls._processes(trace)
        documents: list[SpanDocument] = []
        for span in cls._spans(trace):
            references = cls._references(span)
            parent_id = str(references[0].get("spanID")).casefold() if references else None
            tags = {
                str(tag.get("key")): tag.get("value")
                for tag in cls._tags(span)
                if tag.get("key") is not None
            }
            documents.append(
                SpanDocument(
                    span_id=str(span["spanID"]).casefold(),
                    parent_span_id=parent_id,
                    service=cls._span_service(span, processes) or "unknown",
                    operation=str(span.get("operationName", "")),
                    started_at=epoch_microseconds_to_utc(span["startTime"]),
                    duration_ms=float(span["duration"]) / 1000,
                    error=cls._span_error(span),
                    status_code=cls._span_status(tags),
                    tags=tags,
                )
            )
        return documents

    @staticmethod
    def _spans(trace: dict[str, Any]) -> list[dict[str, Any]]:
        raw = trace.get("spans")
        if not isinstance(raw, list):
            return []
        return [
            cast(dict[str, Any], span) for span in cast(list[Any], raw) if isinstance(span, dict)
        ]

    @staticmethod
    def _processes(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = trace.get("processes")
        if not isinstance(raw, dict):
            return {}
        process_map = cast(dict[Any, Any], raw)
        return {
            str(key): cast(dict[str, Any], value)
            for key, value in process_map.items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _references(span: dict[str, Any]) -> list[dict[str, Any]]:
        raw = span.get("references")
        if not isinstance(raw, list):
            return []
        references: list[dict[str, Any]] = []
        for raw_reference in cast(list[Any], raw):
            if not isinstance(raw_reference, dict):
                continue
            reference = cast(dict[str, Any], raw_reference)
            if reference.get("refType") == "CHILD_OF":
                references.append(reference)
        return references

    @staticmethod
    def _tags(span: dict[str, Any]) -> list[dict[str, Any]]:
        raw = span.get("tags")
        if not isinstance(raw, list):
            return []
        return [cast(dict[str, Any], tag) for tag in cast(list[Any], raw) if isinstance(tag, dict)]

    @classmethod
    def _span_error(cls, span: dict[str, Any]) -> bool:
        tags = {str(tag.get("key")): tag.get("value") for tag in cls._tags(span)}
        return bool(
            tags.get("error")
            or tags.get("otel.status_code") == "ERROR"
            or tags.get("rpc.grpc.status_code") not in {None, 0, "0"}
        )

    @staticmethod
    def _span_status(tags: dict[str, Any]) -> Literal["OK", "ERROR", "UNSET"]:
        if tags.get("error"):
            return "ERROR"
        if "otel.status_code" in tags:
            return normalize_status_code(tags["otel.status_code"])
        if "rpc.grpc.status_code" in tags:
            return "OK" if tags["rpc.grpc.status_code"] in {0, "0"} else "ERROR"
        return "UNSET"

    @staticmethod
    def _span_service(span: dict[str, Any], processes: dict[str, dict[str, Any]]) -> str | None:
        process = processes.get(str(span.get("processID")))
        if not process or not process.get("serviceName"):
            return None
        return normalize_service_name(str(process["serviceName"]))
