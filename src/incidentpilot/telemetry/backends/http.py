from __future__ import annotations

import asyncio
from typing import Any

import httpx

from incidentpilot.telemetry.normalization import TelemetryBackendError

_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


class ReadOnlyJsonClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        max_response_bytes: int = 1_000_000,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_response_bytes = max_response_bytes
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if method not in {"GET", "POST"}:
            raise ValueError("read-only backend client supports only GET and POST")
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json_body,
                )
            except httpx.TimeoutException as exc:
                raise TelemetryBackendError(
                    "UPSTREAM_TIMEOUT", "telemetry backend timed out", retryable=True
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < self._max_retries:
                    if self._retry_backoff_seconds:
                        await asyncio.sleep(self._retry_backoff_seconds * 2**attempt)
                    continue
                raise TelemetryBackendError(
                    "UPSTREAM_UNAVAILABLE", "telemetry backend connection failed", retryable=True
                ) from exc
            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                if self._retry_backoff_seconds:
                    await asyncio.sleep(self._retry_backoff_seconds * 2**attempt)
                continue
            self._raise_for_status(response)
            if len(response.content) > self._max_response_bytes:
                raise TelemetryBackendError(
                    "RESULT_TOO_LARGE",
                    "telemetry backend response exceeded the size limit",
                    retryable=False,
                )
            try:
                return response.json()
            except ValueError as exc:
                raise TelemetryBackendError(
                    "UPSTREAM_UNAVAILABLE",
                    "telemetry backend returned malformed JSON",
                    retryable=False,
                ) from exc
        raise AssertionError("retry loop exhausted unexpectedly")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 403:
            code = "FORBIDDEN"
        elif status == 404:
            code = "NOT_FOUND"
        elif 400 <= status < 500:
            code = "INVALID_ARGUMENT"
        else:
            code = "UPSTREAM_UNAVAILABLE"
        raise TelemetryBackendError(
            code,
            f"telemetry backend returned HTTP {status}",
            retryable=status in _RETRYABLE_STATUS,
        )
