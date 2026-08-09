from __future__ import annotations

from typing import Protocol

from incidentpilot.telemetry.schemas import (
    LogSearch,
    LogSearchResult,
    MetricQuery,
    MetricSeriesSet,
    ServiceDependency,
    TraceDocument,
    TraceSearch,
    TraceSummary,
)


class MetricsBackend(Protocol):
    async def query_range(self, request: MetricQuery) -> MetricSeriesSet: ...


class LogsBackend(Protocol):
    async def search(self, request: LogSearch) -> LogSearchResult: ...


class TracesBackend(Protocol):
    async def search(self, request: TraceSearch) -> list[TraceSummary]: ...

    async def get(self, trace_id: str) -> TraceDocument: ...

    async def get_service_dependencies(self, request: TraceSearch) -> list[ServiceDependency]: ...
