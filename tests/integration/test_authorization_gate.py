from __future__ import annotations

from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from sqlalchemy import select

from incidentpilot.auth.tokens import (
    DevelopmentApprovalGrantProvider,
    DevelopmentApprovalGrantVerifier,
)
from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import IncidentStatus, RiskLevel
from incidentpilot.incidents.models import ActionProposalRow, IncidentRow
from incidentpilot.remediation.approval_service import ApprovalService
from incidentpilot.remediation.authorization_gate import (
    AuthorizationDenied,
    SqlAlchemyApprovalGrantReader,
    SqlAlchemyAuthorizationGate,
)
from incidentpilot.runtime.database import Database
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
WORKER_URL = (
    "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
)


def _keys() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(),
        private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode(),
    )


def _proposal() -> dict[str, object]:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-1", "ev-2"],
        expected_effect="Restart checkout.",
        compensation_plan=CompensationPlan(
            mode="not_applicable", trigger="none", reason="No configuration is changed."
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
        idempotency_key="authorization-gate-1",
    ).model_dump(mode="json")


@pytest.mark.integration
async def test_authorization_gate_rechecks_persisted_grant_and_proposal_digest() -> None:
    database = Database(MIGRATION_URL)
    worker_database = Database(WORKER_URL)
    incident_id = f"inc-authorize-{uuid4().hex}"
    proposal_id = f"proposal-authorize-{uuid4().hex}"
    private_key, public_key = _keys()
    try:
        await seed_local_data(database)
        async with database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="authorization-gate-test",
                    external_id=incident_id,
                    status=IncidentStatus.WAITING_APPROVAL.value,
                    severity="P1",
                    title="Authorization gate test",
                )
            )
            await session.flush()
            session.add(
                ActionProposalRow(
                    id=proposal_id,
                    incident_id=incident_id,
                    payload_json=_proposal(),
                    status="PENDING_APPROVAL",
                    policy_result_json={"allowed": True},
                )
            )
        decision = await ApprovalService(
            database=database,
            grants=DevelopmentApprovalGrantProvider(
                issuer="https://incidentpilot.local",
                audience="action-mcp",
                private_key=private_key,
            ),
        ).decide(
            tenant_id="local",
            incident_id=incident_id,
            proposal_id=proposal_id,
            actor_id="local-operator",
            decision="approve",
            reason="Bounded action approved.",
        )
        gate = SqlAlchemyAuthorizationGate(
            database=worker_database,
            grants=DevelopmentApprovalGrantVerifier(
                issuer="https://incidentpilot.local",
                audience="action-mcp",
                public_key=public_key,
            ),
        )
        await gate.authorize(
            incident_id=incident_id,
            proposal_id=proposal_id,
            approval_id=decision["approval_id"],
        )
        assert await SqlAlchemyApprovalGrantReader(database=worker_database).read_grant(
            incident_id=incident_id,
            proposal_id=proposal_id,
            approval_id=decision["approval_id"],
        )

        async with database.session_factory() as session, session.begin():
            proposal = await session.scalar(
                select(ActionProposalRow).where(ActionProposalRow.id == proposal_id)
            )
            assert proposal is not None
            proposal.payload_json = {**proposal.payload_json, "idempotency_key": "tampered"}

        with pytest.raises(AuthorizationDenied, match="proposal digest"):
            await gate.authorize(
                incident_id=incident_id,
                proposal_id=proposal_id,
                approval_id=decision["approval_id"],
            )
    finally:
        await worker_database.dispose()
        await database.dispose()
