from __future__ import annotations

import re
from typing import Any, cast

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "apikey",
    "email",
    "creditcard",
    "cardnumber",
    "cvv",
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PAYMENT = re.compile(r"(?<![A-Za-z0-9])(?:\d[ -]?){12,18}\d(?![A-Za-z0-9])")

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("_", "").replace("-", "").replace(".", "")
    return any(fragment in normalized for fragment in _SENSITIVE_KEYS)


def _redact_string(value: str) -> str:
    return _PAYMENT.sub("[REDACTED_PAYMENT]", _EMAIL.sub("[REDACTED_EMAIL]", value))


def _redact(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _is_sensitive_key(str(key)) else _redact(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact_data(value: Any) -> Any:
    """Return a redacted copy of JSON-compatible data."""
    return _redact(cast(JsonValue, value))
