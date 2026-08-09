from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text

from incidentpilot.auth.tokens import (
    MAX_APPROVAL_GRANT_LIFETIME,
    DevelopmentApprovalGrantProvider,
)
from incidentpilot.domain.actions import ActionProposal
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.incidents.models import (
    ActionProposalRow,
    AnalysisJobRow,
    ApprovalRow,
    IncidentRow,
)
from incidentpilot.incidents.timeline import AuditTimeline
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.normalization import canonical_digest


class ApprovalService:
    def __init__(
        self,
        *,
        database: Database,
        grants: DevelopmentApprovalGrantProvider,
        operational_metrics: OperationalMetrics | None = None,
    ) -> None:
        self._database = database
        self._grants = grants
        self._operational_metrics = operational_metrics

    async def decide(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        proposal_id: str,
        actor_id: str,
        decision: str,
        reason: str,
    ) -> dict[str, str]:
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"approval:{proposal_id}"},
            )
            existing = await session.scalar(
                select(ApprovalRow).where(ApprovalRow.proposal_id == proposal_id)
            )
            if existing is not None:
                expected_decision = "APPROVED" if decision == "approve" else "REJECTED"
                if existing.actor_id != actor_id or existing.decision != expected_decision:
                    raise ValueError("approval has already been decided")
                return {
                    "approval_id": existing.id,
                    "job_id": f"job_resume_{existing.id}"
                    if existing.decision == "APPROVED"
                    else "",
                }
            incident = await session.get(IncidentRow, incident_id, with_for_update=True)
            proposal = await session.get(ActionProposalRow, proposal_id)
            if (
                incident is None
                or incident.tenant_id != tenant_id
                or proposal is None
                or proposal.incident_id != incident_id
            ):
                raise LookupError("incident or proposal was not found")
            if (
                incident.status != IncidentStatus.WAITING_APPROVAL.value
                or not proposal.policy_result_json.get("allowed")
            ):
                raise ValueError("proposal is not currently approvable")
            if self._operational_metrics is not None:
                self._operational_metrics.record_approval_wait(
                    int((datetime.now(UTC) - incident.updated_at).total_seconds() * 1000),
                    decision=decision,
                )
            parsed = ActionProposal.model_validate(proposal.payload_json)
            approval_id = f"approval_{uuid4().hex}"
            if decision == "reject":
                session.add(
                    ApprovalRow(
                        id=approval_id,
                        proposal_id=proposal_id,
                        actor_id=actor_id,
                        decision="REJECTED",
                        reason=reason,
                    )
                )
                incident.status = IncidentStatus.REJECTED.value
                await AuditTimeline(session).append(
                    event_id=f"audit_{uuid4().hex}",
                    tenant_id=tenant_id,
                    incident_id=incident_id,
                    actor_type="human",
                    actor_id=actor_id,
                    event_type="APPROVAL_RECORDED",
                    payload={"decision": "reject", "proposal_id": proposal_id},
                )
                return {"approval_id": approval_id, "job_id": ""}
            scope = (
                "actions:restart"
                if parsed.action.action_type == "restart_service"
                else "actions:rollback-change"
            )
            token = self._grants.mint_approval_grant(
                tenant_id=tenant_id,
                incident_id=incident_id,
                proposal_id=proposal_id,
                proposal_payload_digest=canonical_digest(proposal.payload_json),
                actor_id=actor_id,
                scope=scope,
            )
            session.add(
                ApprovalRow(
                    id=approval_id,
                    proposal_id=proposal_id,
                    actor_id=actor_id,
                    decision="APPROVED",
                    reason=reason,
                    expires_at=datetime.now(UTC) + MAX_APPROVAL_GRANT_LIFETIME,
                    grant_jws=token,
                    grant_digest=canonical_digest(token),
                )
            )
            proposal.status = "APPROVED"
            incident.status = IncidentStatus.AUTHORIZING.value
            job_id = f"job_resume_{approval_id}"
            session.add(
                AnalysisJobRow(
                    id=job_id,
                    incident_id=incident_id,
                    job_type="RESUME",
                    resume_reference_id=approval_id,
                    status="queued",
                    attempts=0,
                    available_at=datetime.now(UTC),
                )
            )
            await AuditTimeline(session).append(
                event_id=f"audit_{uuid4().hex}",
                tenant_id=tenant_id,
                incident_id=incident_id,
                actor_type="human",
                actor_id=actor_id,
                event_type="APPROVAL_RECORDED",
                payload={"decision": "approve", "proposal_id": proposal_id},
            )
            return {"approval_id": approval_id, "job_id": job_id}
