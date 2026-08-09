from __future__ import annotations

from datetime import datetime, timedelta
from string import Template
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from incidentpilot.domain import DomainModel

ServiceName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")]
PromDuration = Annotated[str, Field(pattern=r"^[1-9][0-9]*[smh]$")]
MetricParameter = Literal["service", "duration", "window", "percentile"]
LogParameter = Literal[
    "services",
    "severities",
    "start",
    "end",
    "query_terms",
    "trace_id",
    "limit",
]
LogSeverity = Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
TraceFailureType = Literal[
    "name_resolution_error",
    "connection_refused",
    "deadline_exceeded",
    "unavailable",
    "not_found",
    "invalid_argument",
    "storage_connection_failure",
    "rate_limited",
]


def _duration_seconds(value: str) -> int:
    amount = int(value[:-1])
    multiplier = {"s": 1, "m": 60, "h": 3600}[value[-1]]
    return amount * multiplier


class MetricTemplate(DomainModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    expression: str = Field(min_length=1, max_length=4000)
    unit: str = Field(min_length=1, max_length=100)
    parameters: list[MetricParameter] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def placeholders_must_match_declared_parameters(self) -> Self:
        placeholders = set(Template(self.expression).get_identifiers())
        if placeholders != set(self.parameters):
            raise ValueError("metric template placeholders must match declared parameters")
        if len(self.parameters) != len(set(self.parameters)):
            raise ValueError("metric template parameters must be unique")
        return self


class MetricTemplateFile(DomainModel):
    schema_version: Literal[1]
    templates: list[MetricTemplate] = Field(min_length=1)


class LogTemplate(DomainModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    index: str = Field(min_length=1, max_length=200)
    parameters: list[LogParameter] = Field(min_length=1, max_length=7)
    max_results: int = Field(ge=1, le=200)

    @model_validator(mode="after")
    def reject_unbounded_index_and_duplicate_parameters(self) -> Self:
        if self.index in {"*", "_all"} or "**" in self.index:
            raise ValueError("unbounded wildcard index is forbidden")
        if len(self.parameters) != len(set(self.parameters)):
            raise ValueError("log template parameters must be unique")
        return self


class LogTemplateFile(DomainModel):
    schema_version: Literal[1]
    templates: list[LogTemplate] = Field(min_length=1)


class MetricRenderRequest(DomainModel):
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    service: ServiceName
    duration: PromDuration = "5m"
    window: PromDuration = "5m"
    percentile: float = Field(default=0.95, gt=0, lt=1)

    @field_validator("duration", "window")
    @classmethod
    def bound_prometheus_duration(cls, value: str) -> str:
        if _duration_seconds(value) > 3600:
            raise ValueError("duration and window must not exceed one hour")
        return value


class MetricQuery(MetricRenderRequest):
    start: datetime
    end: datetime
    step_seconds: int = Field(ge=1, le=300)

    @model_validator(mode="after")
    def bound_metric_time_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("metric query end must not precede start")
        if self.end - self.start > timedelta(hours=1):
            raise ValueError("metric query window must not exceed one hour")
        return self


class MetricPoint(DomainModel):
    timestamp: datetime
    value: float


class MetricSeries(DomainModel):
    labels: dict[str, str]
    points: list[MetricPoint]


class MetricSeriesSet(DomainModel):
    series: list[MetricSeries]
    unit: str
    raw_digest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    truncated: bool = False


class LogSearch(DomainModel):
    services: list[ServiceName] = Field(min_length=1, max_length=10)
    severities: list[LogSeverity] = Field(default_factory=lambda: list[LogSeverity](), max_length=5)
    start: datetime
    end: datetime
    query_terms: list[str] = Field(default_factory=list, max_length=20)
    trace_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("query_terms")
    @classmethod
    def require_exact_terms(cls, terms: list[str]) -> list[str]:
        for term in terms:
            if not term or len(term) > 200:
                raise ValueError("log query terms must contain 1-200 characters")
            if "*" in term or "?" in term:
                raise ValueError("wildcard log terms are forbidden")
        return terms

    @model_validator(mode="after")
    def bound_time_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("log search end must not precede start")
        if self.end - self.start > timedelta(hours=1):
            raise ValueError("log search window must not exceed one hour")
        return self


class LogRecord(DomainModel):
    timestamp: datetime
    service: str
    severity: str
    body: str
    trace_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_digest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LogSearchResult(DomainModel):
    records: list[LogRecord]
    total: int = Field(ge=0)
    truncated: bool = False
    raw_digest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TraceSearch(DomainModel):
    services: list[ServiceName] = Field(min_length=1, max_length=10)
    start: datetime
    end: datetime
    min_duration_ms: int = Field(default=0, ge=0, le=600_000)
    error_only: bool = False
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def bound_trace_time_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("trace search end must not precede start")
        if self.end - self.start > timedelta(hours=1):
            raise ValueError("trace search window must not exceed one hour")
        return self


class TraceErrorSpan(DomainModel):
    service: str
    operation: str
    status_code: Literal["OK", "ERROR", "UNSET"]
    failure_type: TraceFailureType | None = None


class TraceObservation(DomainModel):
    service: str
    operation: str
    attributes: dict[str, bool | int | float | str] = Field(max_length=4)


class TraceSummary(DomainModel):
    trace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    services: list[str]
    started_at: datetime
    duration_ms: float = Field(ge=0)
    error: bool
    error_services: list[str]
    error_spans: list[TraceErrorSpan] = Field(max_length=12)
    observations: list[TraceObservation] = Field(
        default_factory=lambda: list[TraceObservation](), max_length=12
    )
    status_code: Literal["OK", "ERROR", "UNSET"]
    span_count: int = Field(ge=0)
    raw_digest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpanDocument(DomainModel):
    span_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    service: str
    operation: str
    started_at: datetime
    duration_ms: float = Field(ge=0)
    error: bool
    status_code: Literal["OK", "ERROR", "UNSET"]
    tags: dict[str, Any] = Field(default_factory=dict)


class TraceDocument(DomainModel):
    summary: TraceSummary
    spans: list[SpanDocument]


class ServiceDependency(DomainModel):
    parent_service: str
    child_service: str
    call_count: int = Field(ge=1)
    error_count: int = Field(ge=0)
