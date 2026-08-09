from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast
from uuid import uuid4

from incidentpilot.incidents.models import IncidentRow
from incidentpilot.incidents.timeline import AuditTimeline
from incidentpilot.runtime.database import Database

ProgressStage = Literal[
    "intake",
    "triage",
    "investigation",
    "synthesis",
    "planning",
    "safety",
    "authorization",
    "execution",
    "verification",
    "postmortem",
    "evolution",
]
ProgressStatus = Literal["pending", "running", "completed", "waiting", "blocked", "failed"]

_STAGES = frozenset(ProgressStage.__args__)
_STATUSES = frozenset(ProgressStatus.__args__)
_PRIVATE_FIELDS = frozenset(
    {"chain_of_thought", "reasoning", "prompt", "system_prompt", "raw_response"}
)


def progress_payload(
    *,
    stage: str,
    status: str,
    message: str,
    agent: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if stage not in _STAGES:
        raise ValueError(f"unknown progress stage: {stage}")
    if status not in _STATUSES:
        raise ValueError(f"unknown progress status: {status}")
    if not message.strip() or len(message) > 500:
        raise ValueError("progress message must be 1-500 characters")
    safe_details = dict(details or {})
    if _contains_private_field(safe_details):
        raise ValueError("progress details contain private model material")
    payload: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "message": message,
    }
    if agent is not None:
        payload["agent"] = agent
    if safe_details:
        payload["details"] = safe_details
    return payload


class IncidentProgressRecorder:
    """Append public, auditable runtime milestones in independent transactions."""

    def __init__(
        self,
        database: Database,
        *,
        incident_id: str,
        tenant_id: str = "local",
        actor_id: str = "graph-worker",
    ) -> None:
        self._database = database
        self._incident_id = incident_id
        self._tenant_id = tenant_id
        self._actor_id = actor_id

    async def emit(
        self,
        event_type: str,
        *,
        stage: str,
        status: str,
        message: str,
        agent: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        payload = progress_payload(
            stage=stage,
            status=status,
            message=message,
            agent=agent,
            details=details,
        )
        async with self._database.session_factory() as session, session.begin():
            await AuditTimeline(session).append(
                event_id=f"audit_{uuid4().hex}",
                tenant_id=self._tenant_id,
                incident_id=self._incident_id,
                actor_type="worker",
                actor_id=self._actor_id,
                event_type=event_type,
                payload=payload,
            )

    async def set_incident_status(self, status: str) -> None:
        async with self._database.session_factory() as session, session.begin():
            incident = await session.get(IncidentRow, self._incident_id, with_for_update=True)
            if incident is None or incident.tenant_id != self._tenant_id:
                raise LookupError("incident was not found")
            incident.status = status


def _contains_private_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _PRIVATE_FIELDS or _contains_private_field(nested)
            for key, nested in cast(Mapping[object, Any], value).items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_private_field(item) for item in cast(Sequence[Any], value))
    return False
