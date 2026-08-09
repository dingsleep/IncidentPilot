from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from incidentpilot.auth.tokens import (
    ACTION_READ_SCOPE,
    DevelopmentActionCatalogTokenProvider,
    DevelopmentActionCatalogTokenVerifier,
    DevelopmentApprovalGrantProvider,
    DevelopmentApprovalGrantVerifier,
)
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.remediation.approvals import (
    ApprovalAuthorizationFacts,
    InMemoryNonceRegistry,
    authorize_approval_grant,
)

ISSUER = "https://incidentpilot.local"
AUDIENCE = "action-mcp"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _keys() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def _facts(**updates: object) -> ApprovalAuthorizationFacts:
    return ApprovalAuthorizationFacts(
        tenant_id="local",
        incident_id="inc-1",
        proposal_id="proposal-1",
        proposal_payload_digest="a" * 64,
        actor_id="operator-1",
        required_scope="actions:restart",
        incident_status=IncidentStatus.AUTHORIZING,
    ).model_copy(update=updates)


def test_approval_grant_binds_exact_proposal_and_is_single_use() -> None:
    private_key, public_key = _keys()
    token = DevelopmentApprovalGrantProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-1",
    ).mint_approval_grant(
        tenant_id="local",
        incident_id="inc-1",
        proposal_id="proposal-1",
        proposal_payload_digest="a" * 64,
        actor_id="operator-1",
        scope="actions:restart",
    )
    grant = DevelopmentApprovalGrantVerifier(
        issuer=ISSUER, audience=AUDIENCE, public_key=public_key, clock=lambda: NOW
    ).verify_grant(token)

    assert grant is not None
    nonces = InMemoryNonceRegistry()
    assert authorize_approval_grant(grant, _facts(), nonces).allowed
    assert authorize_approval_grant(grant, _facts(), nonces).reason_codes == ["NONCE_REPLAYED"]


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (_facts(tenant_id="other"), "TENANT_MISMATCH"),
        (_facts(proposal_id="proposal-2"), "PROPOSAL_MISMATCH"),
        (_facts(proposal_payload_digest="b" * 64), "PROPOSAL_DIGEST_MISMATCH"),
    ],
)
def test_approval_grant_rejects_rebound_server_facts(
    facts: ApprovalAuthorizationFacts, expected: str
) -> None:
    private_key, public_key = _keys()
    token = DevelopmentApprovalGrantProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-2",
    ).mint_approval_grant(
        tenant_id="local",
        incident_id="inc-1",
        proposal_id="proposal-1",
        proposal_payload_digest="a" * 64,
        actor_id="operator-1",
        scope="actions:restart",
    )
    grant = DevelopmentApprovalGrantVerifier(
        issuer=ISSUER, audience=AUDIENCE, public_key=public_key, clock=lambda: NOW
    ).verify_grant(token)

    assert grant is not None
    decision = authorize_approval_grant(grant, facts, InMemoryNonceRegistry())
    assert decision.reason_codes == [expected]


def test_approval_grant_expiry_and_scope_are_rejected() -> None:
    private_key, public_key = _keys()
    provider = DevelopmentApprovalGrantProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="action scope"):
        provider.mint_approval_grant(
            tenant_id="local",
            incident_id="inc-1",
            proposal_id="proposal-1",
            proposal_payload_digest="a" * 64,
            actor_id="operator-1",
            scope="actions:list",
        )
    token = provider.mint_approval_grant(
        tenant_id="local",
        incident_id="inc-1",
        proposal_id="proposal-1",
        proposal_payload_digest="a" * 64,
        actor_id="operator-1",
        scope="actions:restart",
        lifetime=timedelta(minutes=1),
    )
    assert (
        DevelopmentApprovalGrantVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            public_key=public_key,
            clock=lambda: NOW + timedelta(minutes=1),
        ).verify_grant(token)
        is None
    )


@pytest.mark.asyncio
async def test_catalog_read_token_is_incident_scoped_but_cannot_be_an_approval_grant() -> None:
    private_key, public_key = _keys()
    token = DevelopmentActionCatalogTokenProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
        clock=lambda: NOW,
        jti_factory=lambda: "catalog-1",
    ).mint_catalog_token(tenant_id="local", incident_id="inc-1")

    access = await DevelopmentActionCatalogTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        public_key=public_key,
        clock=lambda: NOW,
    ).verify_token(token)

    assert access is not None
    assert access.scopes == [ACTION_READ_SCOPE]
    assert access.claims is not None and access.claims["incident_id"] == "inc-1"
    assert (
        DevelopmentApprovalGrantVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            public_key=public_key,
            clock=lambda: NOW,
        ).verify_grant(token)
        is None
    )
