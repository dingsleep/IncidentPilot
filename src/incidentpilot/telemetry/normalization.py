from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

BackendErrorCode = Literal[
    "INVALID_ARGUMENT",
    "FORBIDDEN",
    "NOT_FOUND",
    "UPSTREAM_TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "RESULT_TOO_LARGE",
]


class TelemetryBackendError(RuntimeError):
    def __init__(self, code: BackendErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalize_service_name(value: str) -> str:
    return value.strip().casefold().replace("_", "-")


def normalize_status_code(value: Any) -> Literal["OK", "ERROR", "UNSET"]:
    normalized = str(value).upper()
    if normalized in {"ERROR", "STATUS_CODE_ERROR"}:
        return "ERROR"
    if normalized in {"OK", "STATUS_CODE_OK", "0"}:
        return "OK"
    return "UNSET"


def epoch_seconds_to_utc(value: int | float | str) -> datetime:
    return datetime.fromtimestamp(float(value), tz=UTC)


def epoch_microseconds_to_utc(value: int | float | str) -> datetime:
    return datetime.fromtimestamp(float(value) / 1_000_000, tz=UTC)


def parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
