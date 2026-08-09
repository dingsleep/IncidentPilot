from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta

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
from starlette.applications import Starlette

from incidentpilot.auth.tokens import (
    DevelopmentActionCatalogTokenProvider,
    DevelopmentActionCatalogTokenVerifier,
    DevelopmentApprovalGrantProvider,
    DevelopmentApprovalGrantTokenVerifier,
)
from incidentpilot.mcp_servers.actions.server import create_action_mcp
from incidentpilot.mcp_servers.actions.tools import (
    ActionCallerContext,
    ActionToolHandlers,
    InMemoryActionStore,
)
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope

ISSUER = "https://incidentpilot.local"
AUDIENCE = "action-mcp"
BASE_URL = "http://localhost:8102"
TOOL_NAMES = {
    "list_allowed_actions",
    "restart_service",
    "rollback_change",
    "get_action_status",
}


def _keys() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def _token(private_key: str, scope: str) -> str:
    return DevelopmentApprovalGrantProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
    ).mint_approval_grant(
        tenant_id="local",
        incident_id="inc-contract",
        proposal_id="prop-contract",
        proposal_payload_digest="a" * 64,
        actor_id="operator-local",
        scope=scope,
        lifetime=timedelta(minutes=5),
    )


def _catalog_token(private_key: str) -> str:
    return DevelopmentActionCatalogTokenProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
    ).mint_catalog_token(tenant_id="local", incident_id="inc-contract")


@asynccontextmanager
async def _session(app: Starlette, token: str) -> AsyncGenerator[ClientSession]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
        streamable_http_client(f"{BASE_URL}/mcp", http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def _app(public_key: str, store: InMemoryActionStore | None = None) -> Starlette:
    handlers = ActionToolHandlers(
        store=store
        or InMemoryActionStore.for_contract_test(
            tenant_id="local",
            incident_id="inc-contract",
            proposal_id="prop-contract",
            proposal_payload_digest="a" * 64,
        ),
    )
    return create_action_mcp(
        handlers=handlers,
        token_verifier=DevelopmentApprovalGrantTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            public_key=public_key,
        ),
        catalog_token_verifier=DevelopmentActionCatalogTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            public_key=public_key,
        ),
        issuer=ISSUER,
        resource_server_url=f"{BASE_URL}/mcp",
    ).streamable_http_app()


class TimeoutActionStore(InMemoryActionStore):
    async def restart(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        target_service: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        del caller, proposal_id, target_service, idempotency_key
        raise TimeoutError


@pytest.mark.asyncio
async def test_action_mcp_maps_a_bounded_action_timeout() -> None:
    private_key, public_key = _keys()
    app = _app(
        public_key,
        TimeoutActionStore(
            tenant_id="local",
            incident_id="inc-contract",
            proposal_id="prop-contract",
            proposal_payload_digest="a" * 64,
        ),
    )
    async with _session(app, _token(private_key, "actions:restart")) as session:
        timeout = await session.call_tool(
            "restart_service",
            {
                "incident_id": "inc-contract",
                "proposal_id": "prop-contract",
                "target_service": "checkout",
                "idempotency_key": "idem-timeout",
            },
        )

    assert timeout.structuredContent is not None
    assert timeout.structuredContent["error"]["code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.asyncio
async def test_action_tools_are_registered_and_catalog_scope_cannot_execute() -> None:
    private_key, public_key = _keys()
    app = _app(public_key)
    async with _session(app, _catalog_token(private_key)) as session:
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == TOOL_NAMES

        allowed = await session.call_tool(
            "list_allowed_actions",
            {"incident_id": "inc-contract", "target_service": "checkout"},
        )
        assert allowed.structuredContent is not None
        assert allowed.structuredContent["ok"] is True

        forbidden = await session.call_tool(
            "restart_service",
            {
                "incident_id": "inc-contract",
                "proposal_id": "prop-contract",
                "target_service": "checkout",
                "idempotency_key": "idem-001",
            },
        )
        assert forbidden.structuredContent is not None
        assert forbidden.structuredContent["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_action_mcp_rejects_missing_token_and_wrong_origin() -> None:
    private_key, public_key = _keys()
    app = _app(public_key)
    initialize = {
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
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        missing = await client.post(
            "/mcp",
            json=initialize,
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert missing.status_code == 401
        compose_host = await client.post(
            "/mcp",
            json=initialize,
            headers={
                "Accept": "application/json, text/event-stream",
                "Host": "action-mcp:8102",
            },
        )
        assert compose_host.status_code == 401
        origin = await client.post(
            "/mcp",
            json=initialize,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {_catalog_token(private_key)}",
                "Origin": "https://evil.example",
            },
        )
        assert origin.status_code == 403
