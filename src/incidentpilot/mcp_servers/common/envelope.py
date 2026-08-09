from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from incidentpilot.domain import DomainModel

ToolErrorCode = Literal[
    "INVALID_ARGUMENT",
    "FORBIDDEN",
    "NOT_FOUND",
    "UPSTREAM_TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "RESULT_TOO_LARGE",
    "CONFLICT",
]


class ToolError(DomainModel):
    code: ToolErrorCode
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool


class ToolEnvelope(DomainModel):
    ok: bool
    tool_call_id: str = Field(min_length=1, max_length=64)
    evidence_id: str | None = None
    data: dict[str, Any] | list[Any] | None = None
    source_uri: str | None = None
    truncated: bool = False
    error: ToolError | None = None

    @model_validator(mode="after")
    def success_and_error_fields_must_match(self) -> Self:
        if self.ok and self.error is not None:
            raise ValueError("successful tool result cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed tool result must contain an error")
        if not self.ok and (self.evidence_id is not None or self.data is not None):
            raise ValueError("failed tool result cannot contain evidence or data")
        return self
