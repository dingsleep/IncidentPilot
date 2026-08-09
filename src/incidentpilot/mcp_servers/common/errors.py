from __future__ import annotations

from typing import cast

from pydantic import ValidationError

from incidentpilot.mcp_servers.common.envelope import ToolError, ToolErrorCode
from incidentpilot.telemetry.normalization import TelemetryBackendError


def tool_error_from_exception(exc: Exception) -> ToolError:
    if isinstance(exc, TelemetryBackendError):
        return ToolError(
            code=cast(ToolErrorCode, exc.code),
            message=str(exc),
            retryable=exc.retryable,
        )
    if isinstance(exc, (ValidationError, ValueError)):
        return ToolError(
            code="INVALID_ARGUMENT",
            message=str(exc),
            retryable=False,
        )
    if isinstance(exc, TimeoutError):
        return ToolError(
            code="UPSTREAM_TIMEOUT",
            message="Upstream request timed out",
            retryable=True,
        )
    return ToolError(
        code="UPSTREAM_UNAVAILABLE",
        message="Telemetry operation failed",
        retryable=False,
    )
