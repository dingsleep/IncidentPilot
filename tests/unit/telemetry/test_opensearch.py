from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from incidentpilot.telemetry.backends.opensearch import OpenSearchBackend
from incidentpilot.telemetry.normalization import TelemetryBackendError
from incidentpilot.telemetry.schemas import LogSearch

NOW = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)


def _request() -> LogSearch:
    return LogSearch(
        services=["checkout"],
        severities=["ERROR"],
        start=NOW,
        end=NOW,
        query_terms=["charge failed"],
        limit=20,
    )


@pytest.mark.asyncio
async def test_opensearch_builds_bounded_dsl_and_normalizes_hits() -> None:
    captured: dict[str, object] = {}
    responses: list[dict[str, Any]] = [
        {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [
                    {
                        "_source": {
                            "@timestamp": "2026-07-16T14:00:00Z",
                            "body": "charge failed",
                            "resource": {"service.name": "Checkout"},
                            "severity": {"text": "error"},
                            "traceId": "a" * 32,
                            "attributes": {"code": 13},
                        }
                    }
                ],
            }
        },
        {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}},
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        backend = OpenSearchBackend(client=client)
        result = await backend.search(_request())
        empty = await backend.search(_request())

    body = str(captured["body"])
    assert "script" not in body
    assert "wildcard" not in body
    assert '"severity.text.keyword":["ERROR"]' in body
    assert result.records[0].service == "checkout"
    assert result.records[0].severity == "ERROR"
    assert empty.records == []


@pytest.mark.asyncio
async def test_opensearch_samples_each_requested_service_before_truncating() -> None:
    requested_services: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        service = body["query"]["bool"]["filter"][0]["terms"][
            "resource.service.name.keyword"
        ]
        requested_services.append(service)
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-07-16T14:00:00Z",
                                "body": "business log",
                                "resource": {"service.name": service[0]},
                                "severity": {"text": "INFO"},
                                "attributes": {},
                            }
                        }
                    ],
                }
            },
        )

    request = _request().model_copy(
        update={"services": ["checkout", "payment"], "limit": 2}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await OpenSearchBackend(client=client).search(request)

    assert requested_services == [["checkout"], ["payment"]]
    assert [record.service for record in result.records] == ["checkout", "payment"]
    assert result.total == 2


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
async def test_opensearch_error_and_retry_paths(
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
            return httpx.Response(500)
        if mode == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if mode == "malformed":
            return httpx.Response(200, content=b"{")
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * 1001)
        if attempts < 3:
            return httpx.Response(502)
        return httpx.Response(
            200,
            json={"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        backend = OpenSearchBackend(
            client=client,
            max_response_bytes=1000,
            retry_backoff_seconds=0,
        )
        if expected_code is None:
            assert (await backend.search(_request())).records == []
        else:
            with pytest.raises(TelemetryBackendError) as exc:
                await backend.search(_request())
            assert exc.value.code == expected_code
    assert attempts == expected_attempts
