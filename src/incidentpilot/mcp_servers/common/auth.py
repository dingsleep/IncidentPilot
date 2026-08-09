from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from pydantic import Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from incidentpilot.domain import DomainModel
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope, ToolError


class CallerContext(DomainModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    incident_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=200)
    scopes: frozenset[str]


def require_caller(required_scope: str) -> CallerContext:
    access = get_access_token()
    if access is None or required_scope not in access.scopes:
        raise PermissionError(f"required scope is missing: {required_scope}")
    claims: dict[str, Any] = access.claims or {}
    tenant_id = claims.get("tenant_id")
    incident_id = claims.get("incident_id")
    if not isinstance(tenant_id, str) or not isinstance(incident_id, str):
        raise PermissionError("token is missing incident resource claims")
    return CallerContext(
        tenant_id=tenant_id,
        incident_id=incident_id,
        subject=access.subject or access.client_id,
        scopes=frozenset(access.scopes),
    )


def forbidden_envelope(tool_call_id: str, message: str) -> ToolEnvelope:
    return ToolEnvelope(
        ok=False,
        tool_call_id=tool_call_id,
        error=ToolError(code="FORBIDDEN", message=message, retryable=False),
    )


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            body.extend(message.get("body", b""))
            if len(body) > self._max_bytes:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"error":"request_too_large"}',
                    }
                )
                return
            more_body = bool(message.get("more_body", False))
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self._app(scope, replay, send)
