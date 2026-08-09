from __future__ import annotations

from typing import Protocol

from incidentpilot.auth.tokens import ApprovalGrant
from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import IncidentStatus


class ApprovalAuthorizationFacts(DomainModel):
    tenant_id: str
    incident_id: str
    proposal_id: str
    proposal_payload_digest: str
    actor_id: str
    required_scope: str
    incident_status: IncidentStatus


class ApprovalAuthorizationDecision(DomainModel):
    allowed: bool
    reason_codes: list[str]


class NonceRegistry(Protocol):
    def consume(self, nonce: str) -> bool: ...


class InMemoryNonceRegistry:
    """Unit-test registry; Action MCP will consume the nonce from the approvals row."""

    def __init__(self) -> None:
        self._consumed: set[str] = set()

    def consume(self, nonce: str) -> bool:
        if nonce in self._consumed:
            return False
        self._consumed.add(nonce)
        return True


def authorize_approval_grant(
    grant: ApprovalGrant,
    facts: ApprovalAuthorizationFacts,
    nonces: NonceRegistry,
) -> ApprovalAuthorizationDecision:
    """Bind a verified grant to current server facts immediately before execution."""
    checks = (
        (grant.tenant_id == facts.tenant_id, "TENANT_MISMATCH"),
        (grant.incident_id == facts.incident_id, "INCIDENT_MISMATCH"),
        (grant.proposal_id == facts.proposal_id, "PROPOSAL_MISMATCH"),
        (
            grant.proposal_payload_digest == facts.proposal_payload_digest,
            "PROPOSAL_DIGEST_MISMATCH",
        ),
        (grant.actor_id == facts.actor_id, "ACTOR_MISMATCH"),
        (grant.scope == facts.required_scope, "SCOPE_MISMATCH"),
        (facts.incident_status is IncidentStatus.AUTHORIZING, "INCIDENT_NOT_AUTHORIZING"),
    )
    for passed, reason_code in checks:
        if not passed:
            return ApprovalAuthorizationDecision(allowed=False, reason_codes=[reason_code])
    if not nonces.consume(grant.nonce):
        return ApprovalAuthorizationDecision(allowed=False, reason_codes=["NONCE_REPLAYED"])
    return ApprovalAuthorizationDecision(allowed=True, reason_codes=[])
