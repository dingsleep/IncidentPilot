from __future__ import annotations

import asyncio
import json
from math import ceil
from typing import Any, cast

import httpx

from incidentpilot.telemetry.backends.http import ReadOnlyJsonClient
from incidentpilot.telemetry.normalization import (
    TelemetryBackendError,
    canonical_digest,
    normalize_service_name,
    parse_utc_timestamp,
)
from incidentpilot.telemetry.schemas import LogRecord, LogSearch, LogSearchResult


class OpenSearchBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:9200",
        index: str = "otel-logs-*",
        max_response_bytes: int = 2_000_000,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self._http = ReadOnlyJsonClient(
            client=client,
            base_url=base_url,
            max_response_bytes=max_response_bytes,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        self._index = index

    async def search(self, request: LogSearch) -> LogSearchResult:
        per_service_limit = ceil(request.limit / len(request.services))
        payloads = await asyncio.gather(
            *(
                self._http.request_json(
                    "POST",
                    f"/{self._index}/_search",
                    json_body=self._dsl(
                        request.model_copy(
                            update={"services": [service], "limit": per_service_limit}
                        )
                    ),
                )
                for service in request.services
            )
        )
        parsed = [self._hits(payload) for payload in payloads]
        records = self._round_robin([records for records, _, _ in parsed], request.limit)
        return LogSearchResult(
            records=records,
            total=sum(total for _, total, _ in parsed),
            truncated=any(truncated for _, _, truncated in parsed),
            raw_digest_sha256=canonical_digest(payloads),
        )

    def _hits(self, raw: Any) -> tuple[list[LogRecord], int, bool]:
        payload = self._payload(raw)
        hits_object = payload.get("hits")
        if not isinstance(hits_object, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE",
                "OpenSearch returned invalid hits",
                retryable=False,
            )
        hits_container = cast(dict[str, Any], hits_object)
        hits = hits_container.get("hits")
        total_object = hits_container.get("total", {})
        if not isinstance(hits, list) or not isinstance(total_object, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE",
                "OpenSearch returned invalid hit metadata",
                retryable=False,
            )
        hit_list = cast(list[Any], hits)
        total_metadata = cast(dict[str, Any], total_object)
        total = int(total_metadata.get("value", len(hit_list)))
        return (
            [self._record(hit) for hit in hit_list],
            total,
            total > len(hit_list) or total_metadata.get("relation") == "gte",
        )

    @staticmethod
    def _round_robin(
        groups: list[list[LogRecord]], limit: int
    ) -> list[LogRecord]:
        records: list[LogRecord] = []
        for index in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if index < len(group):
                    records.append(group[index])
                    if len(records) == limit:
                        return records
        return records

    @staticmethod
    def _dsl(request: LogSearch) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"terms": {"resource.service.name.keyword": request.services}},
            {
                "range": {
                    "@timestamp": {
                        "gte": request.start.isoformat(),
                        "lte": request.end.isoformat(),
                    }
                }
            },
        ]
        if request.severities:
            filters.append(
                {
                    "terms": {
                        "severity.text.keyword": [
                            severity.upper() for severity in request.severities
                        ]
                    }
                }
            )
        if request.trace_id:
            filters.append({"term": {"traceId.keyword": request.trace_id}})
        must = [{"match_phrase": {"body": term}} for term in request.query_terms]
        return {
            "size": request.limit,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"filter": filters, "must": must}},
        }

    @staticmethod
    def _payload(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE",
                "OpenSearch response must be an object",
                retryable=False,
            )
        return cast(dict[str, Any], raw)

    @staticmethod
    def _record(raw: Any) -> LogRecord:
        if not isinstance(raw, dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "OpenSearch hit is invalid", retryable=False
            )
        hit = cast(dict[str, Any], raw)
        if not isinstance(hit.get("_source"), dict):
            raise TelemetryBackendError(
                "UPSTREAM_UNAVAILABLE", "OpenSearch hit is invalid", retryable=False
            )
        source = cast(dict[str, Any], hit["_source"])
        resource_raw = source.get("resource", {})
        severity_raw = source.get("severity", {})
        attributes_raw = source.get("attributes", {})
        resource = cast(dict[str, Any], resource_raw) if isinstance(resource_raw, dict) else {}
        severity = cast(dict[str, Any], severity_raw) if isinstance(severity_raw, dict) else {}
        attributes = (
            cast(dict[str, Any], attributes_raw) if isinstance(attributes_raw, dict) else {}
        )
        service = resource.get("service.name", "unknown")
        body = source.get("body", "")
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False, sort_keys=True)
        trace_id = source.get("traceId")
        return LogRecord(
            timestamp=parse_utc_timestamp(str(source["@timestamp"])),
            service=normalize_service_name(str(service)),
            severity=str(severity.get("text", "UNSPECIFIED")).upper(),
            body=body,
            trace_id=str(trace_id) if trace_id else None,
            attributes=attributes,
            raw_digest_sha256=canonical_digest(source),
        )
