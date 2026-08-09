from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import httpx
import uvicorn
from mcp.server.auth.provider import TokenVerifier
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from incidentpilot.auth.metadata import telemetry_auth_settings
from incidentpilot.auth.tokens import DevelopmentTelemetryTokenVerifier
from incidentpilot.knowledge.retriever import RunbookRetriever
from incidentpilot.mcp_servers.common.auth import (
    RequestSizeLimitMiddleware,
    forbidden_envelope,
    require_caller,
)
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.mcp_servers.telemetry.resources import (
    load_service_catalog,
    register_telemetry_resources,
)
from incidentpilot.mcp_servers.telemetry.tools import TelemetryToolHandlers
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.observability.setup import (
    create_meter_provider,
    create_tracer_provider,
    instrument_httpx,
    instrument_sqlalchemy,
)
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.heartbeat import ProcessHeartbeat
from incidentpilot.telemetry.backends.jaeger import JaegerBackend
from incidentpilot.telemetry.backends.opensearch import OpenSearchBackend
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import (
    LogSearch,
    LogSeverity,
    MetricQuery,
    TraceSearch,
)

ROOT = Path(__file__).parents[4]
DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://telemetry_mcp_role:telemetry-local-only@127.0.0.1:5433/incidentpilot"
)


def create_telemetry_mcp(
    *,
    handlers: TelemetryToolHandlers,
    token_verifier: TokenVerifier,
    issuer: str,
    resource_server_url: str,
    service_catalog: dict[str, Any] | None = None,
) -> FastMCP[Any]:
    mcp = FastMCP(
        "IncidentPilot Telemetry",
        instructions="Read-only, incident-scoped telemetry tools.",
        host="127.0.0.1",
        port=8101,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        token_verifier=token_verifier,
        auth=telemetry_auth_settings(
            issuer=issuer,
            resource_server_url=resource_server_url,
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )
    _register_tools(mcp, handlers)
    register_telemetry_resources(
        mcp,
        handlers=handlers,
        service_catalog=service_catalog,
    )
    return mcp


def _register_tools(mcp: FastMCP[Any], handlers: TelemetryToolHandlers) -> None:
    @mcp.tool(description="Run a registered Prometheus query template.")
    async def query_metrics(
        template_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")],
        service: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")],
        start: datetime,
        end: datetime,
        step_seconds: Annotated[int, Field(ge=1, le=300)],
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:metrics.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.query_metrics(
            caller,
            MetricQuery(
                template_id=template_id,
                service=service,
                start=start,
                end=end,
                step_seconds=step_seconds,
            ),
        )

    @mcp.tool(description="List server-registered metric template names.")
    async def list_metric_names(
        service: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")],
        prefix: Annotated[str, Field(max_length=100)] = "",
        limit: Annotated[int, Field(ge=1, le=100)] = 100,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:metrics.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.list_metric_names(
            caller,
            service=service,
            prefix=prefix,
            limit=limit,
        )

    @mcp.tool(description="Return bounded RED health metrics for registered services.")
    async def get_service_health_snapshot(
        services: Annotated[list[str], Field(min_length=1, max_length=20)],
        window_minutes: Annotated[int, Field(ge=1, le=60)] = 15,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:metrics.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.get_service_health_snapshot(
            caller,
            services=services,
            window_minutes=window_minutes,
        )

    @mcp.tool(description="Search bounded, exact-term OpenSearch logs.")
    async def search_logs(
        services: Annotated[list[str], Field(min_length=1, max_length=10)],
        start: datetime,
        end: datetime,
        query_terms: Annotated[list[str] | None, Field(max_length=20)] = None,
        levels: Annotated[list[LogSeverity] | None, Field(max_length=5)] = None,
        trace_id: Annotated[
            str | None,
            Field(pattern=r"^[a-f0-9]{32}$"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:logs.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.search_logs(
            caller,
            LogSearch(
                services=services,
                severities=levels or [],
                start=start,
                end=end,
                query_terms=query_terms or [],
                trace_id=trace_id,
                limit=limit,
            ),
        )

    @mcp.tool(description="Read bounded adjacent records from existing log Evidence.")
    async def get_log_context(
        evidence_id: Annotated[str, Field(min_length=1, max_length=64)],
        before: Annotated[int, Field(ge=0, le=20)] = 10,
        after: Annotated[int, Field(ge=0, le=20)] = 10,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:logs.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.get_log_context(
            caller,
            evidence_id=evidence_id,
            before=before,
            after=after,
        )

    @mcp.tool(description="Aggregate deterministic error log patterns.")
    async def aggregate_log_patterns(
        services: Annotated[list[str], Field(min_length=1, max_length=10)],
        start: datetime,
        end: datetime,
        limit: Annotated[int, Field(ge=1, le=20)] = 20,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:logs.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.aggregate_log_patterns(
            caller,
            services=services,
            start=start,
            end=end,
            limit=limit,
        )

    @mcp.tool(description="Search bounded Jaeger trace summaries.")
    async def search_traces(
        services: Annotated[list[str], Field(min_length=1, max_length=10)],
        start: datetime,
        end: datetime,
        min_duration_ms: Annotated[int, Field(ge=0, le=600_000)] = 0,
        error_only: bool = False,
        limit: Annotated[int, Field(ge=1, le=20)] = 20,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:traces.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.search_traces(
            caller,
            TraceSearch(
                services=services,
                start=start,
                end=end,
                min_duration_ms=min_duration_ms,
                error_only=error_only,
                limit=limit,
            ),
        )

    @mcp.tool(description="Get one normalized Jaeger trace.")
    async def get_trace(
        trace_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")],
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:traces.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.get_trace(caller, trace_id=trace_id)

    @mcp.tool(description="Derive bounded service dependencies from Jaeger traces.")
    async def get_service_dependencies(
        service: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")],
        start: datetime,
        end: datetime,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:traces.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.get_service_dependencies(
            caller,
            service=service,
            start=start,
            end=end,
        )

    @mcp.tool(description="List redacted public change events.")
    async def list_recent_changes(
        services: Annotated[list[str], Field(min_length=1, max_length=20)],
        start: datetime,
        end: datetime,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:changes.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.list_recent_changes(
            caller,
            services=services,
            start=start,
            end=end,
        )

    @mcp.tool(description="Search versioned runbook sections with bounded PostgreSQL retrieval.")
    async def search_runbooks(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        services: Annotated[list[str], Field(min_length=1, max_length=10)],
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> ToolEnvelope:
        caller = _authorize("telemetry:runbooks.read")
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.search_runbooks(
            caller,
            query=query,
            services=services,
            limit=limit,
        )

    _ = (
        query_metrics,
        list_metric_names,
        get_service_health_snapshot,
        search_logs,
        get_log_context,
        aggregate_log_patterns,
        search_traces,
        get_trace,
        get_service_dependencies,
        list_recent_changes,
        search_runbooks,
    )


def _authorize(scope: str):
    try:
        return require_caller(scope)
    except PermissionError as exc:
        return forbidden_envelope(f"tc_{uuid4().hex}", str(exc))


async def _serve(args: argparse.Namespace) -> None:
    issuer = os.environ.get("INCIDENTPILOT_TOKEN_ISSUER", "https://incidentpilot.local")
    audience = os.environ.get("INCIDENTPILOT_TELEMETRY_AUDIENCE", "telemetry-mcp")
    verifying_key = _required_environment("INCIDENTPILOT_TELEMETRY_VERIFYING_KEY")
    database = Database(
        os.environ.get("INCIDENTPILOT_TELEMETRY_MCP_DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    stop = asyncio.Event()
    heartbeat = ProcessHeartbeat(
        database,
        process_name="telemetry-mcp",
        instance_id=f"{args.host}:{args.port}",
    )
    await heartbeat.ready()
    heartbeat_task = asyncio.create_task(heartbeat.maintain(stop))
    tracer_provider = create_tracer_provider("incidentpilot-telemetry-mcp")
    meter_provider = create_meter_provider("incidentpilot-telemetry-mcp")
    instrument_sqlalchemy(database.engine, tracer_provider)
    catalog = load_service_catalog(ROOT / "service_catalog" / "otel-demo.yaml")
    raw_services = catalog.get("services")
    if not isinstance(raw_services, list):
        raise ValueError("service catalog services must be a list")
    services = {
        str(cast(dict[str, Any], service)["name"])
        for service in cast(list[Any], raw_services)
        if isinstance(service, dict) and "name" in service
    }
    registry = QueryRegistry.from_files(
        metrics_path=ROOT / "query_templates" / "metrics.yaml",
        logs_path=ROOT / "query_templates" / "logs.yaml",
        allowed_services=services,
    )
    client = httpx.AsyncClient(timeout=15, trust_env=False)
    instrument_httpx(client, tracer_provider)
    handlers = TelemetryToolHandlers(
        database=database,
        registry=registry,
        metrics=PrometheusBackend(client=client, registry=registry),
        logs=OpenSearchBackend(client=client),
        traces=JaegerBackend(client=client),
        runbooks=RunbookRetriever(database),
        tracer_provider=tracer_provider,
        operational_metrics=OperationalMetrics(meter_provider),
    )
    resource_server_url = f"http://{args.host}:{args.port}/mcp"
    mcp = create_telemetry_mcp(
        handlers=handlers,
        token_verifier=DevelopmentTelemetryTokenVerifier(
            issuer=issuer,
            audience=audience,
            public_key=verifying_key.replace("\\n", "\n"),
        ),
        issuer=issuer,
        resource_server_url=resource_server_url,
        service_catalog=catalog,
    )
    app = RequestSizeLimitMiddleware(
        mcp.streamable_http_app(),
        max_bytes=args.max_request_bytes,
    )
    try:
        await uvicorn.Server(
            uvicorn.Config(
                app,
                host=args.host,
                port=args.port,
                log_level="info",
            )
        ).serve()
    finally:
        stop.set()
        await heartbeat_task
        await client.aclose()
        await database.dispose()
        tracer_provider.shutdown()
        meter_provider.shutdown()


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-"):
        raise RuntimeError(f"{name} must contain a development Ed25519 public key")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Telemetry MCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--max-request-bytes", type=int, default=1_048_576)
    asyncio.run(_serve(parser.parse_args()))


if __name__ == "__main__":
    main()
