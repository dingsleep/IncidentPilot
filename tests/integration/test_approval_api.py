from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from pydantic import SecretStr
from sqlalchemy import func, select

from incidentpilot.api.main import create_app
from incidentpilot.config import ActionSettings, ApiSettings, Settings
from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import IncidentStatus, RiskLevel
from incidentpilot.incidents.models import (
    ActionProposalRow,
    AnalysisJobRow,
    ApprovalRow,
    AuditEventRow,
    IncidentRow,
)
from incidentpilot.runtime.database import Database
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
OPERATOR = {"X-IncidentPilot-Actor": "local-operator"}


def _private_key() -> str:
    return (
        Ed25519PrivateKey.generate()
        .private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            NoEncryption(),
        )
        .decode()
    )


def _proposal() -> dict[str, object]:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-one", "ev-two"],
        expected_effect="Restart checkout.",
        compensation_plan=CompensationPlan(
            mode="not_applicable", trigger="none", reason="No safe inverse restart exists."
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
        idempotency_key="proposal-idempotency",
    ).model_dump(mode="json")


@pytest.mark.integration
async def test_approve_persists_grant_audit_and_resume_job_once() -> None:
    database = Database(MIGRATION_URL)
    incident_id = f"inc-approval-{uuid4().hex}"
    proposal_id = f"prop-approval-{uuid4().hex}"
    app = create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            actions=ActionSettings(approval_signing_key=SecretStr(_private_key())),
        )
    )
    try:
        await seed_local_data(database)
        async with database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="approval-test",
                    external_id=incident_id,
                    status=IncidentStatus.WAITING_APPROVAL.value,
                    severity="P1",
                    title="Approval API test",
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

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                proposal = await client.get(
                    f"/api/v1/incidents/{incident_id}/proposals/{proposal_id}",
                    headers=OPERATOR,
                )
                current_proposal = await client.get(
                    f"/api/v1/incidents/{incident_id}/proposals/current",
                    headers=OPERATOR,
                )
                first, second = await asyncio.gather(
                    *(
                        client.post(
                            f"/api/v1/incidents/{incident_id}/proposals/{proposal_id}/approval",
                            json={
                                "decision": "approve",
                                "reason": "Evidence supports this bounded action.",
                            },
                            headers=OPERATOR,
                        )
                        for _ in range(2)
                    )
                )

        assert proposal.status_code == 200
        assert current_proposal.status_code == 200
        assert current_proposal.json()["id"] == proposal_id
        assert proposal.json()["proposal"]["action"]["target_service"] == "checkout"
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json() == second.json()
        async with database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            approval = await session.scalar(
                select(ApprovalRow).where(ApprovalRow.proposal_id == proposal_id)
            )
            resume_count = await session.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(
                    AnalysisJobRow.incident_id == incident_id,
                    AnalysisJobRow.job_type == "RESUME",
                )
            )
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(
                    AuditEventRow.incident_id == incident_id,
                    AuditEventRow.event_type == "APPROVAL_RECORDED",
                )
            )
        assert incident is not None and incident.status == IncidentStatus.AUTHORIZING.value
        assert approval is not None and approval.grant_jws and approval.grant_digest
        assert resume_count == 1
        assert audit_count == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_reject_records_audit_without_enqueuing_an_action_resume() -> None:
    database = Database(MIGRATION_URL)
    incident_id = f"inc-reject-{uuid4().hex}"
    proposal_id = f"prop-reject-{uuid4().hex}"
    app = create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            actions=ActionSettings(approval_signing_key=SecretStr(_private_key())),
        )
    )
    try:
        await seed_local_data(database)
        async with database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="approval-reject-test",
                    external_id=incident_id,
                    status=IncidentStatus.WAITING_APPROVAL.value,
                    severity="P1",
                    title="Approval reject test",
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
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v1/incidents/{incident_id}/proposals/{proposal_id}/approval",
                    json={"decision": "reject", "reason": "Insufficient change window."},
                    headers=OPERATOR,
                )
        assert response.status_code == 202
        assert response.json()["job_id"] == ""
        async with database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            resume_count = await session.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(
                    AnalysisJobRow.incident_id == incident_id,
                    AnalysisJobRow.job_type == "RESUME",
                )
            )
        assert incident is not None and incident.status == IncidentStatus.REJECTED.value
        assert resume_count == 0
    finally:
        await database.dispose()
