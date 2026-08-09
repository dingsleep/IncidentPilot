from __future__ import annotations

# pyright: reportCallIssue=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
from datetime import timedelta
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from opentelemetry.sdk.trace import TracerProvider

from incidentpilot.auth.tokens import DevelopmentActionCatalogTokenProvider
from incidentpilot.domain.actions import (
    ActionProposal,
    ActionResult,
    RestartServiceAction,
)
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.observability.attributes import operation_span
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.remediation.executor import SanitizedExecutionOutput


class ActionMcpCallError(RuntimeError):
    pass


class ActionMcpTransport(Protocol):
    async def call(
        self,
        *,
        tool: str,
        arguments: dict[str, object],
        bearer_token: str,
    ) -> ToolEnvelope: ...


class ApprovalGrantReader(Protocol):
    async def read_grant(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        approval_id: str,
    ) -> str: ...


class StreamableHttpActionMcpTransport:
    """One request-scoped MCP session; tokens never enter graph state or logs."""

    def __init__(self, *, endpoint: str, timeout: timedelta = timedelta(seconds=20)) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    async def call(
        self,
        *,
        tool: str,
        arguments: dict[str, object],
        bearer_token: str,
    ) -> ToolEnvelope:
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=self._timeout.total_seconds(),
        )
        async with (
            http_client,
            streamable_http_client(
                self._endpoint,
                http_client=http_client,
            ) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool, arguments)
        if result.structuredContent is None:
            raise ActionMcpCallError("Action MCP returned no structured envelope")
        try:
            return ToolEnvelope.model_validate(result.structuredContent)
        except ValueError as exc:
            raise ActionMcpCallError("Action MCP returned an invalid envelope") from exc


class ActionMcpCatalogClient:
    def __init__(
        self,
        *,
        transport: ActionMcpTransport,
        tokens: DevelopmentActionCatalogTokenProvider,
    ) -> None:
        self._transport = transport
        self._tokens = tokens

    async def list_allowed_actions(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        target_service: str,
    ) -> set[str]:
        token = self._tokens.mint_catalog_token(
            tenant_id=tenant_id,
            incident_id=incident_id,
        )
        envelope = await self._transport.call(
            tool="list_allowed_actions",
            arguments={"incident_id": incident_id, "target_service": target_service},
            bearer_token=token,
        )
        if not envelope.ok:
            raise _error_from_envelope(envelope)
        data = envelope.data
        if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
            raise ActionMcpCallError("Action MCP catalog response is invalid")
        actions = data["actions"]
        if not all(isinstance(action, str) for action in actions):
            raise ActionMcpCallError("Action MCP catalog contains a non-string action")
        return set(actions)


class ApprovedActionMcpClient:
    def __init__(
        self,
        *,
        transport: ActionMcpTransport,
        grants: ApprovalGrantReader,
        tracer_provider: TracerProvider | None = None,
        operational_metrics: OperationalMetrics | None = None,
    ) -> None:
        self._transport = transport
        self._grants = grants
        self._tracer_provider = tracer_provider
        self._operational_metrics = operational_metrics

    async def execute(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        proposal: ActionProposal,
        approval_id: str,
    ) -> ActionResult:
        with operation_span(
            "incidentpilot.action.execute",
            attributes={"gen_ai.operation.name": "execute_tool"},
            provider=self._tracer_provider,
        ):
            try:
                grant = await self._grants.read_grant(
                    incident_id=incident_id,
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                )
                tool, arguments = _action_request(
                    incident_id=incident_id,
                    proposal_id=proposal_id,
                    proposal=proposal,
                )
                envelope = await self._transport.call(
                    tool=tool,
                    arguments=arguments,
                    bearer_token=grant,
                )
                if not envelope.ok:
                    raise _error_from_envelope(envelope)
            except Exception:
                if self._operational_metrics is not None:
                    self._operational_metrics.record_action(
                        proposal.action.action_type, success=False
                    )
                raise
        if self._operational_metrics is not None:
            self._operational_metrics.record_action(proposal.action.action_type, success=True)
        return _action_result(proposal_id=proposal_id, envelope=envelope)


def _action_request(
    *,
    incident_id: str,
    proposal_id: str,
    proposal: ActionProposal,
) -> tuple[str, dict[str, object]]:
    action = proposal.action
    common: dict[str, object] = {
        "incident_id": incident_id,
        "proposal_id": proposal_id,
        "idempotency_key": proposal.idempotency_key,
    }
    if isinstance(action, RestartServiceAction):
        return "restart_service", {**common, "target_service": action.target_service}
    return "rollback_change", {**common, "change_id": action.change_id}


def _action_result(*, proposal_id: str, envelope: ToolEnvelope) -> ActionResult:
    data = envelope.data
    if not isinstance(data, dict):
        raise ActionMcpCallError("Action MCP execution response is invalid")
    status = data.get("status")
    raw_result: Any = data.get("result") if status == "already_applied" else data
    if status not in {"succeeded", "failed", "already_applied"} or not isinstance(raw_result, dict):
        raise ActionMcpCallError("Action MCP execution status is invalid")
    try:
        output = SanitizedExecutionOutput.model_validate(raw_result)
    except ValueError as exc:
        raise ActionMcpCallError("Action MCP execution payload is invalid") from exc
    return ActionResult(
        proposal_id=proposal_id,
        execution_id=output.execution_id,
        status=status,
        started_at=output.started_at,
        finished_at=output.finished_at,
        external_reference=output.reference,
        sanitized_output=output.model_dump(mode="json"),
    )


def _error_from_envelope(envelope: ToolEnvelope) -> ActionMcpCallError:
    error = envelope.error
    if error is None:
        return ActionMcpCallError("Action MCP rejected the request")
    return ActionMcpCallError(f"Action MCP {error.code}: {error.message}")
