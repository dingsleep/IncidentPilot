from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import IncidentStatus, RiskLevel
from incidentpilot.incidents.models import ActionProposalRow, AuditEventRow, IncidentRow
from incidentpilot.remediation.policy import PolicyDecision
from incidentpilot.remediation.workflow_store import SqlAlchemyRemediationWorkflowStore
from incidentpilot.runtime.database import Database
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


def _proposal() -> ActionProposal:
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
        idempotency_key="workflow-store-restart",
    )


@pytest.mark.integration
async def test_pre_interrupt_workflow_effects_are_idempotent() -> None:
    database = Database(MIGRATION_URL)
    incident_id = f"inc-workflow-{uuid4().hex}"
    store = SqlAlchemyRemediationWorkflowStore(database=database)
    try:
        await seed_local_data(database)
        async with database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="workflow-store-test",
                    external_id=incident_id,
                    status=IncidentStatus.DIAGNOSED.value,
                    severity="P1",
                    title="Workflow store test",
                )
            )
        proposal_id = await store.save_proposal(incident_id=incident_id, proposal=_proposal())
        assert proposal_id == await store.save_proposal(
            incident_id=incident_id, proposal=_proposal()
        )
        decision = PolicyDecision(allowed=True, reason_codes=[], assigned_risk=RiskLevel.LOW)
        await store.save_policy_decision(
            incident_id=incident_id,
            proposal_id=proposal_id,
            decision=decision,
        )
        await store.save_policy_decision(
            incident_id=incident_id,
            proposal_id=proposal_id,
            decision=decision,
        )

        async with database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            proposal = await session.get(ActionProposalRow, proposal_id)
            proposal_audits = await session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(
                    AuditEventRow.incident_id == incident_id,
                    AuditEventRow.event_type.in_(
                        ("ACTION_PROPOSAL_CREATED", "ACTION_POLICY_DECIDED")
                    ),
                )
            )
        assert incident is not None and incident.status == IncidentStatus.WAITING_APPROVAL.value
        assert proposal is not None and proposal.status == "PENDING_APPROVAL"
        assert proposal_audits == 2
    finally:
        await database.dispose()
