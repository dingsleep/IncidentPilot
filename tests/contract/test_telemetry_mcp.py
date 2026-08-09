from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text
from starlette.applications import Starlette

from incidentpilot.auth.tokens import (
    DevelopmentTelemetryTokenProvider,
    DevelopmentTelemetryTokenVerifier,
)
from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity
from incidentpilot.mcp_servers.common.auth import CallerContext, RequestSizeLimitMiddleware
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope, ToolError
from incidentpilot.mcp_servers.telemetry.server import create_telemetry_mcp
from incidentpilot.mcp_servers.telemetry.tools import TelemetryToolHandlers
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from incidentpilot.telemetry.backends.jaeger import JaegerBackend
from incidentpilot.telemetry.backends.opensearch import OpenSearchBackend
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import MetricQuery
from scripts.seed_local_data import seed_local_data

ISSUER = "https://incidentpilot.local"
AUDIENCE = "telemetry-mcp"
BASE_URL = "http://localhost:8101"
ROOT = Path(__file__).parents[2]
MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
TELEMETRY_URL = (
    "postgresql+asyncpg://telemetry_mcp_role:telemetry-local-only@127.0.0.1:5433/incidentpilot"
)
TOOL_NAMES = {
    "query_metrics",
    "list_metric_names",
    "get_service_health_snapshot",
    "search_logs",
    "get_log_context",
    "aggregate_log_patterns",
    "search_traces",
    "get_trace",
    "get_service_dependencies",
    "list_recent_changes",
    "search_runbooks",
}


class ContractHandlers(TelemetryToolHandlers):
    def __init__(self) -> None:
        pass

    async def query_metrics(
        self,
        caller: CallerContext,
        request: MetricQuery,
    ) -> ToolEnvelope:
        assert caller.incident_id == "inc-contract"
        if request.service == "timeout":
            return ToolEnvelope(
                ok=False,
                tool_call_id="tc-timeout",
                error=ToolError(
                    code="UPSTREAM_TIMEOUT",
                    message="Prometheus timed out",
                    retryable=True,
                ),
            )
        return ToolEnvelope(
            ok=True,
            tool_call_id="tc-success",
            evidence_id="ev-success",
            data={"series": [], "unit": "ratio", "truncated": False},
            source_uri="http://localhost:9090/graph",
        )


def _keys() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _token(private_key: str, scopes: set[str]) -> str:
    return DevelopmentTelemetryTokenProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
    ).mint_telemetry_token(
        tenant_id="local",
        incident_id="inc-contract",
        scopes=scopes,
        lifetime=timedelta(minutes=5),
    )


@asynccontextmanager
async def _session(app: Starlette, token: str) -> AsyncGenerator[ClientSession]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
        ) as http_client,
        streamable_http_client(
            f"{BASE_URL}/mcp",
            http_client=http_client,
        ) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


@pytest.mark.asyncio
async def test_official_client_lists_tools_and_calls_structured_handler() -> None:
    private_key, public_key = _keys()
    app = create_telemetry_mcp(
        handlers=ContractHandlers(),
        token_verifier=DevelopmentTelemetryTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            public_key=public_key,
        ),
        issuer=ISSUER,
        resource_server_url=f"{BASE_URL}/mcp",
    ).streamable_http_app()
    token = _token(
        private_key,
        {
            "telemetry:metrics.read",
            "telemetry:logs.read",
            "telemetry:traces.read",
            "telemetry:changes.read",
            "telemetry:runbooks.read",
        },
    )

    async with _session(app, token) as session:
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == TOOL_NAMES

        result = await session.call_tool(
            "query_metrics",
            {
                "template_id": "service_error_ratio",
                "service": "checkout",
                "start": datetime.now(UTC).isoformat(),
                "end": datetime.now(UTC).isoformat(),
                "step_seconds": 15,
            },
        )
        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["evidence_id"] == "ev-success"

        invalid = await session.call_tool(
            "query_metrics",
            {
                "template_id": "service_error_ratio",
                "service": "checkout",
                "start": datetime.now(UTC).isoformat(),
                "end": datetime.now(UTC).isoformat(),
                "step_seconds": 0,
            },
        )
        assert invalid.isError is True


@pytest.mark.asyncio
async def test_auth_origin_scope_and_upstream_timeout_contracts() -> None:
    private_key, public_key = _keys()

    def app() -> Starlette:
        return create_telemetry_mcp(
            handlers=ContractHandlers(),
            token_verifier=DevelopmentTelemetryTokenVerifier(
                issuer=ISSUER,
                audience=AUDIENCE,
                public_key=public_key,
            ),
            issuer=ISSUER,
            resource_server_url=f"{BASE_URL}/mcp",
        ).streamable_http_app()

    raw_app = app()
    initialize: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "contract-test", "version": "1"},
        },
    }
    async with (
        raw_app.router.lifespan_context(raw_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=raw_app),
            base_url=BASE_URL,
        ) as client,
    ):
        missing = await client.post(
            "/mcp",
            json=initialize,
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert missing.status_code == 401

        valid_token = _token(private_key, {"telemetry:metrics.read"})
        forbidden_origin = await client.post(
            "/mcp",
            json=initialize,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {valid_token}",
                "Origin": "https://evil.example",
            },
        )
        assert forbidden_origin.status_code == 403

        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == f"{BASE_URL}/mcp"

    size_app = app()
    limited_app = RequestSizeLimitMiddleware(size_app, max_bytes=100)
    async with (
        size_app.router.lifespan_context(size_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=limited_app),
            base_url=BASE_URL,
        ) as client,
    ):
        too_large = await client.post(
            "/mcp",
            content=b"x" * 101,
            headers={"Content-Type": "application/json"},
        )
        assert too_large.status_code == 413

    logs_only = _token(private_key, {"telemetry:logs.read"})
    async with _session(app(), logs_only) as session:
        wrong_scope = await session.call_tool(
            "query_metrics",
            {
                "template_id": "service_error_ratio",
                "service": "checkout",
                "start": datetime.now(UTC).isoformat(),
                "end": datetime.now(UTC).isoformat(),
                "step_seconds": 15,
            },
        )
        assert wrong_scope.structuredContent is not None
        assert wrong_scope.structuredContent["error"]["code"] == "FORBIDDEN"

    metrics_token = _token(private_key, {"telemetry:metrics.read"})
    async with _session(app(), metrics_token) as session:
        timeout = await session.call_tool(
            "query_metrics",
            {
                "template_id": "service_error_ratio",
                "service": "timeout",
                "start": datetime.now(UTC).isoformat(),
                "end": datetime.now(UTC).isoformat(),
                "step_seconds": 15,
            },
        )
        assert timeout.structuredContent is not None
        assert timeout.structuredContent["error"]["code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.integration
async def test_real_mcp_call_persists_evidence_and_tool_call() -> None:
    private_key, public_key = _keys()
    incident_id = f"inc-mcp-{uuid4().hex}"
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    telemetry_database = Database(TELEMETRY_URL)
    await seed_local_data(migration_database)
    try:
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="mcp-contract",
                    title="Telemetry MCP contract",
                    description="",
                    severity=Severity.P3,
                    starts_at=datetime.now(UTC),
                ),
            )
            await uow.commit()

        registry = QueryRegistry.from_files(
            metrics_path=ROOT / "query_templates" / "metrics.yaml",
            logs_path=ROOT / "query_templates" / "logs.yaml",
            allowed_services={"checkout"},
        )
        async with httpx.AsyncClient(timeout=15, trust_env=False) as backend_client:
            handlers = TelemetryToolHandlers(
                database=telemetry_database,
                registry=registry,
                metrics=PrometheusBackend(client=backend_client, registry=registry),
                logs=OpenSearchBackend(client=backend_client),
                traces=JaegerBackend(client=backend_client),
            )
            app = create_telemetry_mcp(
                handlers=handlers,
                token_verifier=DevelopmentTelemetryTokenVerifier(
                    issuer=ISSUER,
                    audience=AUDIENCE,
                    public_key=public_key,
                ),
                issuer=ISSUER,
                resource_server_url=f"{BASE_URL}/mcp",
            ).streamable_http_app()
            token = DevelopmentTelemetryTokenProvider(
                issuer=ISSUER,
                audience=AUDIENCE,
                private_key=private_key,
            ).mint_telemetry_token(
                tenant_id="local",
                incident_id=incident_id,
                scopes={"telemetry:metrics.read"},
                lifetime=timedelta(minutes=5),
            )
            end = datetime.now(UTC)
            async with _session(app, token) as session:
                result = await session.call_tool(
                    "query_metrics",
                    {
                        "template_id": "service_request_rate",
                        "service": "checkout",
                        "start": (end - timedelta(minutes=10)).isoformat(),
                        "end": end.isoformat(),
                        "step_seconds": 15,
                    },
                )
            assert result.isError is False
            assert result.structuredContent is not None
            assert result.structuredContent["ok"] is True
            evidence_id = result.structuredContent["evidence_id"]

            wrong_tenant_token = DevelopmentTelemetryTokenProvider(
                issuer=ISSUER,
                audience=AUDIENCE,
                private_key=private_key,
            ).mint_telemetry_token(
                tenant_id="other",
                incident_id=incident_id,
                scopes={"telemetry:metrics.read"},
                lifetime=timedelta(minutes=5),
            )
            wrong_tenant_app = create_telemetry_mcp(
                handlers=handlers,
                token_verifier=DevelopmentTelemetryTokenVerifier(
                    issuer=ISSUER,
                    audience=AUDIENCE,
                    public_key=public_key,
                ),
                issuer=ISSUER,
                resource_server_url=f"{BASE_URL}/mcp",
            ).streamable_http_app()
            async with _session(wrong_tenant_app, wrong_tenant_token) as session:
                forbidden = await session.call_tool(
                    "query_metrics",
                    {
                        "template_id": "service_request_rate",
                        "service": "checkout",
                        "start": (end - timedelta(minutes=10)).isoformat(),
                        "end": end.isoformat(),
                        "step_seconds": 15,
                    },
                )
            assert forbidden.structuredContent is not None
            assert forbidden.structuredContent["error"]["code"] == "FORBIDDEN"

        async with migration_database.engine.connect() as connection:
            evidence_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM evidence "
                    "WHERE id = :evidence_id AND incident_id = :incident_id"
                ),
                {"evidence_id": evidence_id, "incident_id": incident_id},
            )
            tool_call_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM tool_calls "
                    "WHERE incident_id = :incident_id AND tool_name = 'query_metrics' "
                    "AND status = 'SUCCESS'"
                ),
                {"incident_id": incident_id},
            )
        assert evidence_count == 1
        assert tool_call_count == 1
    finally:
        await telemetry_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()
