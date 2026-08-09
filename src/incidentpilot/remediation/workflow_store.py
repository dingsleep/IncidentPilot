from __future__ import annotations

import hashlib

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.domain.actions import ActionProposal
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.incidents.models import ActionProposalRow, AuditEventRow, IncidentRow
from incidentpilot.incidents.timeline import AuditTimeline
from incidentpilot.remediation.policy import PolicyDecision
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.normalization import canonical_digest


class SqlAlchemyRemediationWorkflowStore:
    """Idempotent persistence for graph effects that occur before an interrupt."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def save_proposal(self, *, incident_id: str, proposal: ActionProposal) -> str:
        proposal_id = _stable_id(
            "proposal", f"{incident_id}:{canonical_digest(proposal.model_dump(mode='json'))}"
        )
        payload = proposal.model_dump(mode="json")
        async with self._database.session_factory() as session, session.begin():
            incident = await session.get(IncidentRow, incident_id, with_for_update=True)
            if incident is None:
                raise LookupError("incident was not found")
            if incident.status not in {
                IncidentStatus.DIAGNOSED.value,
                IncidentStatus.PLANNING.value,
            }:
                raise DomainInvariantError("incident is not eligible for remediation planning")
            await session.execute(
                insert(ActionProposalRow)
                .values(
                    id=proposal_id,
                    incident_id=incident_id,
                    payload_json=payload,
                    status="PENDING_POLICY",
                    policy_result_json={},
                )
                .on_conflict_do_nothing(index_elements=[ActionProposalRow.id])
            )
            existing = await session.get(ActionProposalRow, proposal_id)
            if existing is None or existing.payload_json != payload:
                raise DomainInvariantError("proposal identity conflicts with persisted payload")
            if incident.status == IncidentStatus.DIAGNOSED.value:
                incident.status = IncidentStatus.PLANNING.value
            await self._audit_once(
                session,
                event_id=_stable_id("audit-plan", proposal_id),
                tenant_id=incident.tenant_id,
                incident_id=incident_id,
                event_type="ACTION_PROPOSAL_CREATED",
                payload={"proposal_id": proposal_id, "payload_digest": canonical_digest(payload)},
            )
        return proposal_id

    async def save_policy_decision(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        decision: PolicyDecision,
    ) -> None:
        async with self._database.session_factory() as session, session.begin():
            incident = await session.get(IncidentRow, incident_id, with_for_update=True)
            proposal = await session.get(ActionProposalRow, proposal_id)
            if incident is None or proposal is None or proposal.incident_id != incident_id:
                raise LookupError("incident or proposal was not found")
            payload = decision.model_dump(mode="json")
            if proposal.policy_result_json and proposal.policy_result_json != payload:
                raise DomainInvariantError("policy result changed for an existing proposal")
            proposal.policy_result_json = payload
            if decision.allowed:
                proposal.status = "PENDING_APPROVAL"
                incident.status = IncidentStatus.WAITING_APPROVAL.value
            else:
                proposal.status = "POLICY_REJECTED"
                incident.status = IncidentStatus.POLICY_REJECTED.value
            await self._audit_once(
                session,
                event_id=_stable_id("audit-policy", proposal_id),
                tenant_id=incident.tenant_id,
                incident_id=incident_id,
                event_type="ACTION_POLICY_DECIDED",
                payload={"proposal_id": proposal_id, **payload},
            )

    @staticmethod
    async def _audit_once(
        session: AsyncSession,
        *,
        event_id: str,
        tenant_id: str,
        incident_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        existing = await session.get(AuditEventRow, event_id)
        if existing is None:
            await AuditTimeline(session).append(
                event_id=event_id,
                tenant_id=tenant_id,
                incident_id=incident_id,
                actor_type="worker",
                actor_id="graph-worker",
                event_type=event_type,
                payload=payload,
            )


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:32]}"
