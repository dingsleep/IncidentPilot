from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from incidentpilot.auth.tokens import DevelopmentApprovalGrantVerifier
from incidentpilot.domain.actions import ActionProposal, RestartServiceAction
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.incidents.models import ActionProposalRow, ApprovalRow, IncidentRow
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.normalization import canonical_digest


class AuthorizationDenied(RuntimeError):
    pass


class SqlAlchemyApprovalGrantReader:
    """Read the short-lived bearer only after the graph's authorization node succeeds."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def read_grant(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        approval_id: str,
    ) -> str:
        async with self._database.session_factory() as session:
            approval = await session.scalar(
                select(ApprovalRow)
                .join(ActionProposalRow, ActionProposalRow.id == ApprovalRow.proposal_id)
                .join(IncidentRow, IncidentRow.id == ActionProposalRow.incident_id)
                .where(
                    ApprovalRow.id == approval_id,
                    ApprovalRow.proposal_id == proposal_id,
                    ActionProposalRow.incident_id == incident_id,
                    ApprovalRow.decision == "APPROVED",
                    ApprovalRow.nonce_used_at.is_(None),
                    IncidentRow.status == IncidentStatus.AUTHORIZING.value,
                )
            )
        if approval is None or not approval.grant_jws:
            raise AuthorizationDenied("approval grant is no longer available")
        return approval.grant_jws


class SqlAlchemyAuthorizationGate:
    """Worker-side defense in depth before the Action MCP performs its own atomic check."""

    def __init__(self, *, database: Database, grants: DevelopmentApprovalGrantVerifier) -> None:
        self._database = database
        self._grants = grants

    async def authorize(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        approval_id: str,
    ) -> None:
        async with self._database.session_factory() as session:
            row = await session.execute(
                select(IncidentRow, ActionProposalRow, ApprovalRow)
                .join(ActionProposalRow, ActionProposalRow.incident_id == IncidentRow.id)
                .join(ApprovalRow, ApprovalRow.proposal_id == ActionProposalRow.id)
                .where(
                    IncidentRow.id == incident_id,
                    ActionProposalRow.id == proposal_id,
                    ApprovalRow.id == approval_id,
                )
            )
            record = row.one_or_none()
        if record is None:
            raise AuthorizationDenied("approval, proposal, or incident was not found")
        incident, proposal_row, approval = record
        if (
            incident.status != IncidentStatus.AUTHORIZING.value
            or proposal_row.status != "APPROVED"
            or not bool(proposal_row.policy_result_json.get("allowed"))
            or approval.decision != "APPROVED"
            or approval.nonce_used_at is not None
            or approval.expires_at is None
            or approval.expires_at <= datetime.now(UTC)
            or not approval.grant_jws
        ):
            raise AuthorizationDenied("persisted approval is no longer executable")
        grant = self._grants.verify_grant(approval.grant_jws)
        if grant is None:
            raise AuthorizationDenied("approval grant is invalid or expired")
        proposal = _proposal_or_denied(proposal_row)
        expected_scope = (
            "actions:restart"
            if isinstance(proposal.action, RestartServiceAction)
            else "actions:rollback-change"
        )
        if canonical_digest(proposal_row.payload_json) != grant.proposal_payload_digest:
            raise AuthorizationDenied("approval proposal digest no longer matches")
        if (
            grant.tenant_id != incident.tenant_id
            or grant.incident_id != incident_id
            or grant.proposal_id != proposal_id
            or grant.actor_id != approval.actor_id
            or grant.scope != expected_scope
        ):
            raise AuthorizationDenied("approval grant no longer matches server facts")


def _proposal_or_denied(row: ActionProposalRow) -> ActionProposal:
    try:
        return ActionProposal.model_validate(row.payload_json)
    except ValueError as exc:
        raise AuthorizationDenied("stored action proposal is invalid") from exc
