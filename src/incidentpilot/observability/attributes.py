from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Protocol

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span

from incidentpilot.observability.redaction import redact_data


class SpanAttributes(Protocol):
    def set_attribute(self, key: str, value: str | bool | int | float) -> None: ...


def set_safe_attributes(span: SpanAttributes, attributes: dict[str, Any]) -> None:
    """Set only scalar, redacted attributes on an OpenTelemetry span."""
    for key, value in redact_data(attributes).items():
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)


@contextmanager
def operation_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    provider: TracerProvider | None = None,
) -> Generator[Span]:
    """Create a redacted operational span, or a no-op span when tracing is unset."""
    tracer = (
        provider.get_tracer("incidentpilot")
        if provider is not None
        else trace.get_tracer("incidentpilot")
    )
    with tracer.start_as_current_span(name) as span:
        if attributes:
            set_safe_attributes(span, attributes)
        yield span
