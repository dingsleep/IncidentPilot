from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from incidentpilot.api.dependencies import get_runtime, require_role
from incidentpilot.api.errors import ApiProblem
from incidentpilot.domain import DomainModel
from incidentpilot.domain.actions import ActionProposal
from incidentpilot.incidents.models import ActionProposalRow, IncidentRow

router = APIRouter(prefix="/incidents", tags=["approvals"])


class ApprovalRequest(DomainModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str = Field(min_length=1, max_length=1000)


class ActionProposalView(DomainModel):
    id: str
    status: str
    proposal: ActionProposal
    policy: dict[str, object]


def _proposal_view(row: ActionProposalRow) -> ActionProposalView:
    return ActionProposalView(
        id=row.id,
        status=row.status,
        proposal=ActionProposal.model_validate(row.payload_json),
        policy=row.policy_result_json,
    )


async def get_current_proposal(
    incident_id: str,
    request: Request,
) -> ActionProposalView | None:
    actor = require_role(request, "viewer")
    async with get_runtime(request).database.session_factory() as session:
        row = await session.scalar(
            select(ActionProposalRow)
            .join(IncidentRow, IncidentRow.id == ActionProposalRow.incident_id)
            .where(
                ActionProposalRow.incident_id == incident_id,
                IncidentRow.tenant_id == actor.tenant_id,
            )
            .limit(1)
        )
    return _proposal_view(row) if row is not None else None


async def get_proposal(
    incident_id: str,
    proposal_id: str,
    request: Request,
) -> ActionProposalView:
    actor = require_role(request, "viewer")
    async with get_runtime(request).database.session_factory() as session:
        row = await session.scalar(
            select(ActionProposalRow)
            .join(IncidentRow, IncidentRow.id == ActionProposalRow.incident_id)
            .where(
                ActionProposalRow.id == proposal_id,
                ActionProposalRow.incident_id == incident_id,
                IncidentRow.tenant_id == actor.tenant_id,
            )
        )
    if row is None:
        raise ApiProblem(
            status=404,
            code="PROPOSAL_NOT_FOUND",
            title="Not Found",
            detail="The proposal was not found.",
        )
    return _proposal_view(row)


async def decide_approval(
    incident_id: str, proposal_id: str, payload: ApprovalRequest, request: Request
) -> dict[str, str]:
    actor = require_role(request, "operator")
    service = get_runtime(request).approvals
    if service is None:
        raise ApiProblem(
            status=503,
            code="ACTION_APPROVAL_DISABLED",
            title="Service Unavailable",
            detail="Approval signing is not configured.",
        )
    try:
        return await service.decide(
            tenant_id=actor.tenant_id,
            incident_id=incident_id,
            proposal_id=proposal_id,
            actor_id=actor.actor_id,
            decision=payload.decision,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code="PROPOSAL_NOT_FOUND",
            title="Not Found",
            detail="The proposal was not found.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status=409,
            code="APPROVAL_CONFLICT",
            title="Conflict",
            detail="The proposal cannot be decided now.",
        ) from exc


router.add_api_route(
    "/{incident_id}/proposals/current",
    get_current_proposal,
    methods=["GET"],
)
router.add_api_route(
    "/{incident_id}/proposals/{proposal_id}",
    get_proposal,
    methods=["GET"],
)
router.add_api_route(
    "/{incident_id}/proposals/{proposal_id}/approval",
    decide_approval,
    methods=["POST"],
    status_code=202,
)
