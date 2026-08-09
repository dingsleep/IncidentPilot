from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from incidentpilot.auth.tokens import DevelopmentActionCatalogTokenProvider
from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import RiskLevel
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope, ToolError
from incidentpilot.remediation.action_mcp_client import (
    ActionMcpCallError,
    ActionMcpCatalogClient,
    ApprovedActionMcpClient,
)


def _proposal() -> ActionProposal:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-1", "ev-2"],
        expected_effect="Restart checkout.",
        compensation_plan=CompensationPlan(
            mode="not_applicable", trigger="none", reason="No config is changed."
        ),
        verification_checks=[
            VerificationCheck(
                service="checkout",
                metric="error_ratio",
                query_template_id="service_error_ratio",
                comparator="lt",
                threshold=0.05,
                observation_seconds=30,
            )
        ],
        idempotency_key="restart-checkout-1",
    )


def _private_key() -> str:
    return (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )


@pytest.mark.asyncio
async def test_catalog_client_uses_a_short_lived_read_token_and_validates_response() -> None:
    captured: dict[str, object] = {}

    class Transport:
        async def call(
            self, *, tool: str, arguments: dict[str, object], bearer_token: str
        ) -> ToolEnvelope:
            captured.update(tool=tool, arguments=arguments, bearer_token=bearer_token)
            return ToolEnvelope(
                ok=True,
                tool_call_id="tc-catalog",
                data={"actions": ["restart_service"]},
            )

    client = ActionMcpCatalogClient(
        transport=Transport(),
        tokens=DevelopmentActionCatalogTokenProvider(
            issuer="https://issuer.example",
            audience="action-mcp",
            private_key=_private_key(),
            clock=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )

    actions = await client.list_allowed_actions(
        tenant_id="local", incident_id="inc-1", target_service="checkout"
    )

    assert actions == {"restart_service"}
    assert captured["tool"] == "list_allowed_actions"
    assert captured["arguments"] == {"incident_id": "inc-1", "target_service": "checkout"}
    assert isinstance(captured["bearer_token"], str)


@pytest.mark.asyncio
async def test_approved_client_rejects_an_action_mcp_error_without_executing_fallback() -> None:
    class Grants:
        async def read_grant(self, *, incident_id: str, proposal_id: str, approval_id: str) -> str:
            return "approval-grant"

    class Transport:
        async def call(
            self, *, tool: str, arguments: dict[str, object], bearer_token: str
        ) -> ToolEnvelope:
            del tool, arguments, bearer_token
            return ToolEnvelope(
                ok=False,
                tool_call_id="tc-denied",
                error=ToolError(
                    code="FORBIDDEN", message="grant no longer matches", retryable=False
                ),
            )

    client = ApprovedActionMcpClient(transport=Transport(), grants=Grants())
    with pytest.raises(ActionMcpCallError, match="FORBIDDEN"):
        await client.execute(
            incident_id="inc-1",
            proposal_id="proposal-1",
            proposal=_proposal(),
            approval_id="approval-1",
        )
