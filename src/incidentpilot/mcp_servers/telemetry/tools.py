from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import select

from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.incidents.models import (
    ChangeEventRow,
    EvidenceRow,
    IncidentRow,
    ToolCallRow,
)
from incidentpilot.knowledge.retriever import RunbookRetriever
from incidentpilot.mcp_servers.common.auth import CallerContext
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.mcp_servers.common.errors import tool_error_from_exception
from incidentpilot.observability.attributes import operation_span
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.observability.redaction import redact_data
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.evidence_store import (
    EvidenceStore,
    EvidenceWrite,
    SqlAlchemyEvidenceRepository,
)
from incidentpilot.telemetry.normalization import (
    TelemetryBackendError,
    canonical_digest,
)
from incidentpilot.telemetry.ports import LogsBackend, MetricsBackend, TracesBackend
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import (
    LogSearch,
    MetricQuery,
    MetricSeriesSet,
    TraceSearch,
)

logger = logging.getLogger(__name__)
ToolData = dict[str, Any] | list[Any]
Operation = Callable[[], Awaitable[ToolData]]
_VARIABLE_TOKEN = re.compile(
    r"\b(?:[0-9a-f]{16,}|[0-9]+(?:\.[0-9]+)?)\b",
    flags=re.IGNORECASE,
)


class TelemetryToolHandlers:
    def __init__(
        self,
        *,
        database: Database,
        registry: QueryRegistry,
        metrics: MetricsBackend,
        logs: LogsBackend,
        traces: TracesBackend,
        runbooks: RunbookRetriever | None = None,
        tracer_provider: TracerProvider | None = None,
        operational_metrics: OperationalMetrics | None = None,
    ) -> None:
        self._database = database
        self._registry = registry
        self._metrics = metrics
        self._logs = logs
        self._traces = traces
        self._runbooks = runbooks
        self._tracer_provider = tracer_provider
        self._operational_metrics = operational_metrics

    async def query_metrics(
        self,
        caller: CallerContext,
        request: MetricQuery,
    ) -> ToolEnvelope:
        async def operation() -> ToolData:
            result = await self._metrics.query_range(request)
            return result.model_dump(mode="json")

        return await self._execute(
            caller=caller,
            tool_name="query_metrics",
            query=request.model_dump(mode="json"),
            kind=EvidenceKind.METRIC,
            source_system="prometheus",
            source_uri="prometheus://query_range",
            observed_range=TimeRange(start=request.start, end=request.end),
            operation=operation,
        )

    async def list_metric_names(
        self,
        caller: CallerContext,
        *,
        service: str,
        prefix: str = "",
        limit: int = 100,
    ) -> ToolEnvelope:
        if service not in self._registry.allowed_services:
            return await self._invalid(caller, "list_metric_names", "service is not registered")
        if not 1 <= limit <= 100:
            return await self._invalid(caller, "list_metric_names", "limit must be 1-100")
        names = sorted(name for name in self._registry.metric_ids if name.startswith(prefix))[
            :limit
        ]
        now = datetime.now(UTC)
        return await self._execute(
            caller=caller,
            tool_name="list_metric_names",
            query={"service": service, "prefix": prefix, "limit": limit},
            kind=EvidenceKind.METRIC,
            source_system="query-registry",
            source_uri="incidentpilot://query-templates/metrics",
            observed_range=TimeRange(start=now, end=now),
            operation=lambda: _value({"metric_names": names}),
        )

    async def get_service_health_snapshot(
        self,
        caller: CallerContext,
        *,
        services: list[str],
        window_minutes: int = 15,
    ) -> ToolEnvelope:
        if not services or len(services) > 20:
            return await self._invalid(
                caller,
                "get_service_health_snapshot",
                "services must contain 1-20 entries",
            )
        if not 1 <= window_minutes <= 60:
            return await self._invalid(
                caller,
                "get_service_health_snapshot",
                "window_minutes must be 1-60",
            )
        unknown = set(services) - self._registry.allowed_services
        if unknown:
            return await self._invalid(
                caller,
                "get_service_health_snapshot",
                f"service is not registered: {sorted(unknown)[0]}",
            )
        end = datetime.now(UTC)
        start = end - timedelta(minutes=window_minutes)
        step_seconds = min(300, max(15, window_minutes * 3))

        async def operation() -> ToolData:
            requests = [
                MetricQuery(
                    template_id=template_id,
                    service=service,
                    start=start,
                    end=end,
                    step_seconds=step_seconds,
                    duration=f"{window_minutes}m",
                    window=f"{window_minutes}m",
                )
                for service in services
                for template_id in (
                    "service_request_rate",
                    "service_error_ratio",
                    "service_latency_p95",
                    "container_memory_usage",
                )
            ]
            results = await asyncio.gather(
                *(self._metrics.query_range(request) for request in requests)
            )
            snapshots: dict[str, dict[str, Any]] = {service: {} for service in services}
            for request, result in zip(requests, results, strict=True):
                snapshots[request.service][request.template_id] = _latest_metric_value(result)
            return {"snapshots": snapshots}

        return await self._execute(
            caller=caller,
            tool_name="get_service_health_snapshot",
            query={"services": services, "window_minutes": window_minutes},
            kind=EvidenceKind.METRIC,
            source_system="prometheus",
            source_uri="prometheus://health-snapshot",
            observed_range=TimeRange(start=start, end=end),
            operation=operation,
        )

    async def search_logs(
        self,
        caller: CallerContext,
        request: LogSearch,
    ) -> ToolEnvelope:
        async def operation() -> ToolData:
            result = await self._logs.search(request)
            return result.model_dump(mode="json")

        return await self._execute(
            caller=caller,
            tool_name="search_logs",
            query=request.model_dump(mode="json"),
            kind=EvidenceKind.LOG,
            source_system="opensearch",
            source_uri="opensearch://otel-logs",
            observed_range=TimeRange(start=request.start, end=request.end),
            operation=operation,
        )

    async def get_log_context(
        self,
        caller: CallerContext,
        *,
        evidence_id: str,
        before: int = 10,
        after: int = 10,
    ) -> ToolEnvelope:
        if not 0 <= before <= 20 or not 0 <= after <= 20:
            return await self._invalid(
                caller,
                "get_log_context",
                "before and after must be 0-20",
            )
        now = datetime.now(UTC)

        async def operation() -> ToolData:
            async with self._database.session_factory() as session:
                row = await session.get(EvidenceRow, evidence_id)
            if row is None or row.incident_id != caller.incident_id:
                raise TelemetryBackendError(
                    "NOT_FOUND",
                    "log evidence was not found",
                    retryable=False,
                )
            if row.kind != EvidenceKind.LOG.value or not isinstance(row.raw_json, dict):
                raise TelemetryBackendError(
                    "INVALID_ARGUMENT",
                    "evidence is not log evidence",
                    retryable=False,
                )
            records = row.raw_json.get("records")
            if not isinstance(records, list):
                records = []
            return {
                "source_evidence_id": evidence_id,
                "records": records[: before + after + 1],
            }

        return await self._execute(
            caller=caller,
            tool_name="get_log_context",
            query={"evidence_id": evidence_id, "before": before, "after": after},
            kind=EvidenceKind.LOG,
            source_system="evidence-store",
            source_uri=f"incidents://{caller.incident_id}/evidence/{evidence_id}",
            observed_range=TimeRange(start=now, end=now),
            operation=operation,
        )

    async def aggregate_log_patterns(
        self,
        caller: CallerContext,
        *,
        services: list[str],
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> ToolEnvelope:
        if not 1 <= limit <= 20:
            return await self._invalid(
                caller,
                "aggregate_log_patterns",
                "limit must be 1-20",
            )
        request = LogSearch(
            services=services,
            severities=["ERROR", "FATAL"],
            start=start,
            end=end,
            limit=200,
        )

        async def operation() -> ToolData:
            result = await self._logs.search(request)
            patterns = Counter(
                _VARIABLE_TOKEN.sub("<value>", record.body) for record in result.records
            )
            return {
                "patterns": [
                    {"pattern": pattern, "count": count}
                    for pattern, count in patterns.most_common(limit)
                ],
                "truncated": result.truncated,
            }

        return await self._execute(
            caller=caller,
            tool_name="aggregate_log_patterns",
            query={
                "services": services,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": limit,
            },
            kind=EvidenceKind.LOG,
            source_system="opensearch",
            source_uri="opensearch://otel-logs/patterns",
            observed_range=TimeRange(start=start, end=end),
            operation=operation,
        )

    async def search_traces(
        self,
        caller: CallerContext,
        request: TraceSearch,
    ) -> ToolEnvelope:
        async def operation() -> ToolData:
            result = await self._traces.search(request)
            return {"traces": [trace.model_dump(mode="json") for trace in result]}

        return await self._execute(
            caller=caller,
            tool_name="search_traces",
            query=request.model_dump(mode="json"),
            kind=EvidenceKind.TRACE,
            source_system="jaeger",
            source_uri="jaeger://traces",
            observed_range=TimeRange(start=request.start, end=request.end),
            operation=operation,
        )

    async def get_trace(
        self,
        caller: CallerContext,
        *,
        trace_id: str,
    ) -> ToolEnvelope:
        now = datetime.now(UTC)

        async def operation() -> ToolData:
            result = await self._traces.get(trace_id)
            return result.model_dump(mode="json")

        return await self._execute(
            caller=caller,
            tool_name="get_trace",
            query={"trace_id": trace_id},
            kind=EvidenceKind.TRACE,
            source_system="jaeger",
            source_uri=f"jaeger://traces/{trace_id}",
            observed_range=TimeRange(start=now, end=now),
            operation=operation,
        )

    async def get_service_dependencies(
        self,
        caller: CallerContext,
        *,
        service: str,
        start: datetime,
        end: datetime,
    ) -> ToolEnvelope:
        request = TraceSearch(services=[service], start=start, end=end, limit=20)

        async def operation() -> ToolData:
            result = await self._traces.get_service_dependencies(request)
            return {"dependencies": [dependency.model_dump(mode="json") for dependency in result]}

        return await self._execute(
            caller=caller,
            tool_name="get_service_dependencies",
            query={"service": service, "start": start.isoformat(), "end": end.isoformat()},
            kind=EvidenceKind.TOPOLOGY,
            source_system="jaeger",
            source_uri="jaeger://dependencies",
            observed_range=TimeRange(start=start, end=end),
            operation=operation,
        )

    async def list_recent_changes(
        self,
        caller: CallerContext,
        *,
        services: list[str],
        start: datetime,
        end: datetime,
    ) -> ToolEnvelope:
        if not services or len(services) > 20:
            return await self._invalid(
                caller,
                "list_recent_changes",
                "services must contain 1-20 entries",
            )
        observed_range = TimeRange(start=start, end=end)

        async def operation() -> ToolData:
            async with self._database.session_factory() as session:
                rows = (
                    await session.scalars(
                        select(ChangeEventRow)
                        .where(
                            ChangeEventRow.service.in_(services),
                            ChangeEventRow.occurred_at >= start,
                            ChangeEventRow.occurred_at <= end,
                        )
                        .order_by(ChangeEventRow.occurred_at.desc())
                    )
                ).all()
            return {
                "changes": [
                    {
                        "change_id": row.id,
                        "service": row.service,
                        "change_type": row.change_type,
                        "summary": row.summary,
                        "occurred_at": row.occurred_at.isoformat(),
                    }
                    for row in rows
                ]
            }

        return await self._execute(
            caller=caller,
            tool_name="list_recent_changes",
            query={
                "services": services,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            kind=EvidenceKind.CHANGE,
            source_system="incidentpilot-change-store",
            source_uri="incidentpilot://changes",
            observed_range=observed_range,
            operation=operation,
        )

    async def search_runbooks(
        self,
        caller: CallerContext,
        *,
        query: str,
        services: list[str],
        limit: int = 5,
    ) -> ToolEnvelope:
        if self._runbooks is None:
            return await self._invalid(caller, "search_runbooks", "runbook index is unavailable")
        if not query.strip() or len(query) > 500:
            return await self._invalid(caller, "search_runbooks", "query must be 1-500 chars")
        if not services or len(services) > 10:
            return await self._invalid(
                caller,
                "search_runbooks",
                "services must contain 1-10 entries",
            )
        unknown = set(services) - self._registry.allowed_services
        if unknown:
            return await self._invalid(
                caller,
                "search_runbooks",
                f"service is not registered: {sorted(unknown)[0]}",
            )
        if not 1 <= limit <= 20:
            return await self._invalid(caller, "search_runbooks", "limit must be 1-20")
        now = datetime.now(UTC)
        retriever = self._runbooks

        async def operation() -> ToolData:
            hits = await retriever.search(
                query=query,
                services=services,
                limit=limit,
            )
            return {"hits": [hit.model_dump(mode="json") for hit in hits]}

        return await self._execute(
            caller=caller,
            tool_name="search_runbooks",
            query={"query": query, "services": services, "limit": limit},
            kind=EvidenceKind.RUNBOOK,
            source_system="incidentpilot-runbooks",
            source_uri="runbooks://search",
            observed_range=TimeRange(start=now, end=now),
            operation=operation,
        )

    async def get_runbook_resource(
        self,
        caller: CallerContext,
        *,
        checksum: str,
    ) -> ToolData:
        if self._runbooks is None:
            raise LookupError("runbook index is unavailable")
        await self._assert_incident_ownership(caller)
        hit = await self._runbooks.get_by_checksum(checksum)
        if hit is None:
            raise LookupError("runbook section was not found")
        return hit.model_dump(mode="json")

    async def get_evidence_resource(
        self,
        caller: CallerContext,
        *,
        incident_id: str,
        evidence_id: str,
    ) -> ToolData:
        if incident_id != caller.incident_id:
            raise PermissionError("resource incident does not match token")
        await self._assert_incident_ownership(caller)
        async with self._database.session_factory() as session:
            row = await session.get(EvidenceRow, evidence_id)
        if row is None or row.incident_id != incident_id:
            raise LookupError("evidence was not found")
        return {
            "id": row.id,
            "incident_id": row.incident_id,
            "kind": row.kind,
            "summary": row.summary,
            "raw_json": row.raw_json,
            "digest": row.digest,
            "source_uri": row.source_uri,
            "truncated": row.truncated,
        }

    async def _invalid(
        self,
        caller: CallerContext,
        tool_name: str,
        message: str,
    ) -> ToolEnvelope:
        async def operation() -> ToolData:
            raise TelemetryBackendError(
                "INVALID_ARGUMENT",
                message,
                retryable=False,
            )

        now = datetime.now(UTC)
        return await self._execute(
            caller=caller,
            tool_name=tool_name,
            query={},
            kind=EvidenceKind.METRIC,
            source_system="incidentpilot",
            source_uri=None,
            observed_range=TimeRange(start=now, end=now),
            operation=operation,
        )

    async def _execute(
        self,
        *,
        caller: CallerContext,
        tool_name: str,
        query: dict[str, Any],
        kind: EvidenceKind,
        source_system: str,
        source_uri: str | None,
        observed_range: TimeRange,
        operation: Operation,
    ) -> ToolEnvelope:
        tool_call_id = f"tc_{uuid4().hex}"
        started = perf_counter()
        owned = False
        with operation_span(
            "incidentpilot.mcp.execute_tool",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "incidentpilot.tool.name": tool_name,
            },
            provider=self._tracer_provider,
        ) as span:
            try:
                await self._assert_incident_ownership(caller)
                owned = True
                raw = cast(ToolData, redact_data(await operation()))
                duration_ms = int((perf_counter() - started) * 1000)
                evidence_id = await self._persist_success(
                    caller=caller,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    query=query,
                    kind=kind,
                    source_system=source_system,
                    source_uri=source_uri,
                    observed_range=observed_range,
                    raw=raw,
                    duration_ms=duration_ms,
                )
                truncated = bool(raw.get("truncated", False)) if isinstance(raw, dict) else False
                span.set_attribute("incidentpilot.tool.success", True)
                if self._operational_metrics is not None:
                    self._operational_metrics.record_tool(tool_name, duration_ms, success=True)
                return ToolEnvelope(
                    ok=True,
                    tool_call_id=tool_call_id,
                    evidence_id=evidence_id,
                    data=raw,
                    source_uri=source_uri,
                    truncated=truncated,
                )
            except Exception as exc:
                error = tool_error_from_exception(exc)
                span.set_attribute("incidentpilot.tool.success", False)
                span.set_attribute("error.type", error.code)
                if self._operational_metrics is not None:
                    self._operational_metrics.record_tool(
                        tool_name,
                        int((perf_counter() - started) * 1000),
                        success=False,
                    )
                if owned:
                    try:
                        await self._record_failure(
                            caller=caller,
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            query=query,
                            duration_ms=int((perf_counter() - started) * 1000),
                            status=error.code,
                        )
                    except Exception:
                        logger.error("Failed to persist telemetry tool failure")
                return ToolEnvelope(
                    ok=False,
                    tool_call_id=tool_call_id,
                    error=error,
                )

    async def _assert_incident_ownership(self, caller: CallerContext) -> None:
        async with self._database.session_factory() as session:
            tenant_id = await session.scalar(
                select(IncidentRow.tenant_id).where(IncidentRow.id == caller.incident_id)
            )
        if tenant_id is None:
            raise TelemetryBackendError(
                "NOT_FOUND",
                "incident was not found",
                retryable=False,
            )
        if tenant_id != caller.tenant_id:
            raise TelemetryBackendError(
                "FORBIDDEN",
                "incident is not owned by token tenant",
                retryable=False,
            )

    async def _persist_success(
        self,
        *,
        caller: CallerContext,
        tool_call_id: str,
        tool_name: str,
        query: dict[str, Any],
        kind: EvidenceKind,
        source_system: str,
        source_uri: str | None,
        observed_range: TimeRange,
        raw: ToolData,
        duration_ms: int,
    ) -> str:
        async with self._database.session_factory() as session, session.begin():
            evidence = await EvidenceStore(
                repository=SqlAlchemyEvidenceRepository(session)
            ).persist(
                EvidenceWrite(
                    incident_id=caller.incident_id,
                    kind=kind,
                    source_system=source_system,
                    query=query,
                    raw_json=raw,
                    observed_range=observed_range,
                    source_uri=source_uri,
                    truncated=bool(raw.get("truncated", False)) if isinstance(raw, dict) else False,
                    collected_at=datetime.now(UTC),
                )
            )
            session.add(
                ToolCallRow(
                    id=tool_call_id,
                    incident_id=caller.incident_id,
                    agent_name=caller.subject,
                    tool_name=tool_name,
                    args_digest=canonical_digest(query),
                    result_digest=canonical_digest(raw),
                    duration_ms=duration_ms,
                    status="SUCCESS",
                )
            )
        return evidence.id

    async def _record_failure(
        self,
        *,
        caller: CallerContext,
        tool_call_id: str,
        tool_name: str,
        query: dict[str, Any],
        duration_ms: int,
        status: str,
    ) -> None:
        async with self._database.session_factory() as session, session.begin():
            session.add(
                ToolCallRow(
                    id=tool_call_id,
                    incident_id=caller.incident_id,
                    agent_name=caller.subject,
                    tool_name=tool_name,
                    args_digest=canonical_digest(query),
                    result_digest=None,
                    duration_ms=duration_ms,
                    status=status,
                )
            )


async def _value(value: ToolData) -> ToolData:
    return value


def _latest_metric_value(result: MetricSeriesSet) -> dict[str, Any]:
    values = [series.points[-1].value for series in result.series if series.points]
    return {
        "value": values[-1] if values else None,
        "unit": result.unit,
        "series_count": len(result.series),
        "truncated": result.truncated,
    }
