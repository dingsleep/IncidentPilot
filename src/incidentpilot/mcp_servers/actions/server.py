from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from incidentpilot.auth.metadata import telemetry_auth_settings
from incidentpilot.auth.tokens import ACTION_READ_SCOPE, ApprovalGrant
from incidentpilot.mcp_servers.actions.tools import ActionCallerContext, ActionToolHandlers
from incidentpilot.mcp_servers.common.auth import forbidden_envelope, require_caller
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.telemetry.normalization import canonical_digest


def create_action_mcp(
    *,
    handlers: ActionToolHandlers,
    token_verifier: TokenVerifier,
    catalog_token_verifier: TokenVerifier | None = None,
    issuer: str,
    resource_server_url: str,
) -> FastMCP[Any]:
    resolved_verifier = _ActionTokenVerifier(
        approval_verifier=token_verifier,
        catalog_verifier=catalog_token_verifier,
    )
    mcp = FastMCP(
        "IncidentPilot Actions",
        instructions="Approval-gated, bounded remediation tools.",
        host="127.0.0.1",
        port=8102,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        token_verifier=resolved_verifier,
        auth=telemetry_auth_settings(issuer=issuer, resource_server_url=resource_server_url),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "[::1]:*",
                "action-mcp:8102",
            ],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )

    @mcp.tool(description="List allowed bounded action schemas without executing one.")
    async def list_allowed_actions(
        incident_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
        target_service: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")],
    ) -> ToolEnvelope:
        caller = _authorize_catalog(ACTION_READ_SCOPE)
        if isinstance(caller, ToolEnvelope):
            return caller
        if caller.incident_id != incident_id:
            return forbidden_envelope(f"tc_{uuid4().hex}", "incident ownership mismatch")
        return await handlers.list_allowed_actions(caller, target_service=target_service)

    @mcp.tool(description="Restart one server-approved catalog service after approval.")
    async def restart_service(
        incident_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
        proposal_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
        target_service: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")],
        idempotency_key: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> ToolEnvelope:
        caller = _authorize("actions:restart")
        if isinstance(caller, ToolEnvelope):
            return caller
        if caller.incident_id != incident_id:
            return forbidden_envelope(f"tc_{uuid4().hex}", "incident ownership mismatch")
        return await handlers.restart_service(
            caller,
            proposal_id=proposal_id,
            target_service=target_service,
            idempotency_key=idempotency_key,
        )

    @mcp.tool(description="Restore one server-mapped flagd change after approval.")
    async def rollback_change(
        incident_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
        proposal_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
        change_id: Annotated[str, Field(pattern=r"^chg_[a-zA-Z0-9_]+$")],
        idempotency_key: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> ToolEnvelope:
        caller = _authorize("actions:rollback-change")
        if isinstance(caller, ToolEnvelope):
            return caller
        if caller.incident_id != incident_id:
            return forbidden_envelope(f"tc_{uuid4().hex}", "incident ownership mismatch")
        return await handlers.rollback_change(
            caller,
            proposal_id=proposal_id,
            change_id=change_id,
            idempotency_key=idempotency_key,
        )

    @mcp.tool(description="Get a sanitized action execution status.")
    async def get_action_status(
        execution_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
    ) -> ToolEnvelope:
        caller = _authorize_catalog(ACTION_READ_SCOPE)
        if isinstance(caller, ToolEnvelope):
            return caller
        return await handlers.get_action_status(caller, execution_id=execution_id)

    del list_allowed_actions, restart_service, rollback_change, get_action_status
    return mcp


class _ActionTokenVerifier:
    def __init__(
        self,
        *,
        approval_verifier: TokenVerifier,
        catalog_verifier: TokenVerifier | None,
    ) -> None:
        self._approval_verifier = approval_verifier
        self._catalog_verifier = catalog_verifier

    async def verify_token(self, token: str):
        if self._catalog_verifier is not None:
            catalog_access = await self._catalog_verifier.verify_token(token)
            if catalog_access is not None:
                return catalog_access
        return await self._approval_verifier.verify_token(token)


def _authorize_catalog(scope: str):
    try:
        caller = require_caller(scope)
        return caller
    except PermissionError as exc:
        return forbidden_envelope(f"tc_{uuid4().hex}", str(exc))


def _authorize(scope: str):
    try:
        caller = require_caller(scope)
        access = get_access_token()
        if access is None or access.claims is None:
            raise PermissionError("approval grant is missing")
        grant = ApprovalGrant.model_validate(access.claims)
        return ActionCallerContext(
            **caller.model_dump(),
            approval_grant=grant,
            grant_digest=canonical_digest(access.token),
        )
    except PermissionError as exc:
        return forbidden_envelope(f"tc_{uuid4().hex}", str(exc))
