from fastapi import APIRouter, Request
from sqlalchemy import select

from incidentpilot.api.dependencies import get_runtime, require_role
from incidentpilot.domain import DomainModel
from incidentpilot.incidents.models import CandidateVersionRow, PromotionGateRecordRow

router = APIRouter(prefix="/evolution", tags=["evolution"])


class EvolutionCandidateView(DomainModel):
    id: str
    kind: str
    base_version: str
    target_failure_label: str
    target_component: str
    generator_model: str
    digest: str
    status: str
    diff: str
    gate_statuses: list[str]
    rejection_reasons: list[str]
    gate_records: list[dict[str, object]]


async def list_candidates(request: Request) -> list[EvolutionCandidateView]:
    require_role(request, "viewer")
    async with get_runtime(request).database.session_factory() as session:
        candidates = (
            await session.scalars(
                select(CandidateVersionRow).order_by(CandidateVersionRow.created_at.desc())
            )
        ).all()
        records = (
            await session.scalars(
                select(PromotionGateRecordRow).order_by(PromotionGateRecordRow.created_at.desc())
            )
        ).all()
    by_candidate: dict[str, list[PromotionGateRecordRow]] = {}
    for record in records:
        by_candidate.setdefault(record.candidate_id, []).append(record)
    return [
        EvolutionCandidateView(
            id=item.id,
            kind=item.kind,
            base_version=item.base_version,
            target_failure_label=item.target_failure_label,
            target_component=item.target_component,
            generator_model=item.generator_model,
            digest=item.digest,
            status=item.status,
            diff=item.diff,
            gate_statuses=[record.status for record in by_candidate.get(item.id, [])],
            rejection_reasons=[
                record.human_rejection_reason
                for record in by_candidate.get(item.id, [])
                if record.human_rejection_reason
            ],
            gate_records=[
                {
                    "status": record.status,
                    "decision": record.decision_json,
                    "human_rejection_reason": record.human_rejection_reason,
                }
                for record in by_candidate.get(item.id, [])
            ],
        )
        for item in candidates
    ]


router.add_api_route("/candidates", list_candidates, methods=["GET"])
