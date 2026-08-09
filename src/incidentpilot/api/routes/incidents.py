from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from incidentpilot.api.dependencies import get_runtime, require_role
from incidentpilot.api.errors import ApiProblem
from incidentpilot.api.sse import SseRepository, parse_event_id, stream_events
from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import ExecutionMode, IncidentStatus, Severity
from incidentpilot.incidents.service import (
    EvidenceView,
    IncidentPage,
    IncidentView,
    TimelineEventView,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


class ManualIncidentRequest(DomainModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=4000)
    severity: Severity
    service: str = Field(min_length=1, max_length=200)
    starts_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    start_analysis: bool = True
    execution_mode: ExecutionMode = ExecutionMode.REVIEW


async def create_incident(payload: ManualIncidentRequest, request: Request) -> dict[str, object]:
    actor = require_role(request, "operator")
    incident, job_id = await get_runtime(request).incidents.create_manual(
        tenant_id=actor.tenant_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        service=payload.service,
        starts_at=payload.starts_at,
        start_analysis=payload.start_analysis,
        execution_mode=payload.execution_mode,
    )
    return {"incident": incident.model_dump(mode="json"), "job_id": job_id}


async def list_incidents(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    severity: Severity | None = None,
    status: IncidentStatus | None = None,
    service: str | None = Query(default=None, min_length=1, max_length=200),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> IncidentPage:
    actor = require_role(request, "viewer")
    try:
        return await get_runtime(request).incidents.list_incidents(
            tenant_id=actor.tenant_id,
            limit=limit,
            cursor=cursor,
            severity=severity,
            status=status,
            service=service,
            created_from=created_from,
            created_to=created_to,
        )
    except ValueError as exc:
        raise ApiProblem(
            status=400,
            code="INVALID_CURSOR_OR_TIME_RANGE",
            title="Bad Request",
            detail="The incident cursor or time range is invalid.",
        ) from exc


async def get_incident(incident_id: str, request: Request) -> IncidentView:
    actor = require_role(request, "viewer")
    incident = await get_runtime(request).incidents.get_incident(
        tenant_id=actor.tenant_id,
        incident_id=incident_id,
    )
    if incident is None:
        raise _not_found()
    return incident


async def start_analysis(incident_id: str, request: Request) -> dict[str, object]:
    actor = require_role(request, "operator")
    job = await get_runtime(request).incidents.ensure_start_job(
        tenant_id=actor.tenant_id,
        incident_id=incident_id,
    )
    if job is None:
        raise _not_found()
    return job.model_dump(mode="json")


async def list_evidence(incident_id: str, request: Request) -> list[EvidenceView]:
    actor = require_role(request, "viewer")
    evidence = await get_runtime(request).incidents.list_evidence(
        tenant_id=actor.tenant_id,
        incident_id=incident_id,
    )
    if evidence is None:
        raise _not_found()
    return evidence


async def get_evidence(
    incident_id: str,
    evidence_id: str,
    request: Request,
) -> EvidenceView:
    actor = require_role(request, "viewer")
    evidence = await get_runtime(request).incidents.get_evidence(
        tenant_id=actor.tenant_id,
        incident_id=incident_id,
        evidence_id=evidence_id,
    )
    if evidence is None:
        raise _not_found()
    return evidence


async def list_timeline(incident_id: str, request: Request) -> list[TimelineEventView]:
    actor = require_role(request, "viewer")
    timeline = await get_runtime(request).incidents.list_timeline(
        tenant_id=actor.tenant_id,
        incident_id=incident_id,
    )
    if timeline is None:
        raise _not_found()
    return timeline


async def subscribe_events(incident_id: str, request: Request) -> StreamingResponse:
    actor = require_role(request, "viewer")
    runtime = get_runtime(request)
    incident = await runtime.incidents.get_incident(
        tenant_id=actor.tenant_id,
        incident_id=incident_id,
    )
    if incident is None:
        raise _not_found()
    last_event_id = request.headers.get("last-event-id")
    try:
        if last_event_id is not None:
            parse_event_id(last_event_id)
    except ValueError as exc:
        raise ApiProblem(
            status=400,
            code="INVALID_LAST_EVENT_ID",
            title="Bad Request",
            detail="Last-Event-ID is invalid.",
        ) from exc
    return StreamingResponse(
        stream_events(
            SseRepository(runtime.database),
            tenant_id=actor.tenant_id,
            incident_id=incident_id,
            last_event_id=last_event_id,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _not_found() -> ApiProblem:
    return ApiProblem(
        status=404,
        code="INCIDENT_NOT_FOUND",
        title="Not Found",
        detail="The incident or requested resource was not found.",
    )


router.add_api_route("", create_incident, methods=["POST"], status_code=201)
router.add_api_route("", list_incidents, methods=["GET"])
router.add_api_route("/{incident_id}", get_incident, methods=["GET"])
router.add_api_route("/{incident_id}/analysis", start_analysis, methods=["POST"], status_code=202)
router.add_api_route("/{incident_id}/timeline", list_timeline, methods=["GET"])
router.add_api_route("/{incident_id}/events", subscribe_events, methods=["GET"])
router.add_api_route("/{incident_id}/evidence", list_evidence, methods=["GET"])
router.add_api_route(
    "/{incident_id}/evidence/{evidence_id}",
    get_evidence,
    methods=["GET"],
)
