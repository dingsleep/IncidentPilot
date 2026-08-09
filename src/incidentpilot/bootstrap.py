from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from incidentpilot.domain.actions import VerificationResult
from incidentpilot.domain.alerts import AlertPayload, TimeRange
from incidentpilot.domain.diagnosis import Diagnosis, RootCauseHypothesis
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.incidents.models import (
    AlertRow,
    DiagnosisRow,
    HypothesisRow,
    IncidentRow,
    ToolCallRow,
    VerificationResultRow,
)
from incidentpilot.incidents.repository import SqlAlchemyIncidentRepository
from incidentpilot.incidents.timeline import AuditTimeline
from incidentpilot.orchestration.state import InvestigationBudget, ReportArtifact
from incidentpilot.runtime.database import Database


@asynccontextmanager
async def open_checkpoint_saver(
    connection_string: str,
    *,
    setup: bool = False,
) -> AsyncGenerator[AsyncPostgresSaver]:
    """Own the checkpointer connection lifecycle outside LangGraph state."""
    async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
        if setup:
            await saver.setup()
        yield saver


class DatabaseInitialStateLoader:
    def __init__(
        self,
        database: Database,
        *,
        max_waves: int = 2,
        max_read_calls: int = 12,
    ) -> None:
        self._database = database
        self._max_waves = max_waves
        self._max_read_calls = max_read_calls

    async def load(self, incident_id: str) -> dict[str, Any]:
        async with self._database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            alert_row = (
                await session.scalars(
                    select(AlertRow).where(AlertRow.incident_id == incident_id).limit(1)
                )
            ).one_or_none()
        if incident is None or alert_row is None:
            raise LookupError("incident or alert was not found")
        alert = AlertPayload.model_validate(alert_row.payload_json)
        service = alert.service_hint or alert.labels.get("service")
        if not service:
            raise ValueError("alert must provide a server-validated service hint")
        end = datetime.now(UTC)
        start = max(alert.starts_at - timedelta(minutes=10), end - timedelta(hours=1))
        return {
            "incident_id": incident.id,
            "tenant_id": incident.tenant_id,
            "status": IncidentStatus(incident.status).value,
            "alert": alert.model_dump(mode="json"),
            "scoped_services": [service],
            "time_range": TimeRange(start=start, end=end).model_dump(mode="json"),
            "investigation_budget": InvestigationBudget(
                wave=1,
                max_waves=self._max_waves,
                read_calls_used=0,
                max_read_calls=self._max_read_calls,
            ).model_dump(mode="json"),
            "reports": [],
            "evidence_ids": [],
            "tool_call_ids": [],
            "hypotheses": [],
            "diagnosis": None,
            "errors": [],
        }


class DatabaseReferenceStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        async with self._database.session_factory() as session:
            return await SqlAlchemyIncidentRepository(session).get_evidence(evidence_id)

    async def tool_call_belongs_to_incident(
        self,
        tool_call_id: str,
        incident_id: str,
    ) -> bool:
        async with self._database.session_factory() as session:
            owner = await session.scalar(
                select(ToolCallRow.incident_id).where(ToolCallRow.id == tool_call_id)
            )
        return owner == incident_id


class SqlAlchemyGraphResultSink:
    def __init__(
        self,
        database: Database,
        *,
        model_profile: str,
        prompt_version: str,
    ) -> None:
        self._database = database
        self._model_profile = model_profile
        self._prompt_version = prompt_version

    async def persist(self, incident_id: str, state: dict[str, Any]) -> None:
        status = IncidentStatus(state["status"])
        diagnosis = _optional_model(state.get("diagnosis"), Diagnosis)
        hypotheses = [
            RootCauseHypothesis.model_validate(item)
            for item in cast(list[Any], state.get("hypotheses", []))
        ]
        async with self._database.session_factory() as session, session.begin():
            incident = await session.get(IncidentRow, incident_id, with_for_update=True)
            if incident is None:
                raise LookupError("incident was not found")
            incident.status = status.value
            if diagnosis is not None:
                await session.execute(
                    insert(DiagnosisRow)
                    .values(
                        id=_stable_id("diag", incident_id),
                        incident_id=incident_id,
                        payload_json=diagnosis.model_dump(mode="json"),
                        model_profile=self._model_profile,
                        prompt_version=self._prompt_version,
                    )
                    .on_conflict_do_nothing(index_elements=[DiagnosisRow.id])
                )
            for hypothesis in hypotheses:
                await session.execute(
                    insert(HypothesisRow)
                    .values(
                        id=_stable_id("hyp", f"{incident_id}:{hypothesis.id}"),
                        incident_id=incident_id,
                        wave=InvestigationBudget.model_validate(state["investigation_budget"]).wave,
                        payload_json=hypothesis.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing(index_elements=[HypothesisRow.id])
                )
            verification = _optional_model(state.get("verification_result"), VerificationResult)
            if verification is not None:
                raw_action_result = state.get("action_result")
                if not isinstance(raw_action_result, dict):
                    raise ValueError("verification result requires an action result")
                action_result = cast(dict[str, Any], raw_action_result)
                execution_id = action_result.get("execution_id")
                if not isinstance(execution_id, str) or not execution_id:
                    raise ValueError("verification result requires an execution ID")
                await session.execute(
                    insert(VerificationResultRow)
                    .values(
                        id=_stable_id("verify", execution_id),
                        execution_id=execution_id,
                        payload_json=verification.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing(index_elements=[VerificationResultRow.id])
                )
            await AuditTimeline(session).append(
                event_id=f"audit_{uuid4().hex}",
                tenant_id=incident.tenant_id,
                incident_id=incident_id,
                actor_type="worker",
                actor_id="graph-worker",
                event_type="GRAPH_COMPLETED",
                payload=_timeline_payload(state),
            )


def _timeline_payload(state: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": IncidentStatus(state["status"]).value,
        "evidence_ids": list(cast(list[str], state.get("evidence_ids", []))),
    }
    if diagnosis := _optional_model(state.get("diagnosis"), Diagnosis):
        payload["diagnosis"] = {
            "root_cause_service": diagnosis.root_cause_service,
            "confidence": diagnosis.confidence,
            "evidence_ids": diagnosis.evidence_ids,
        }
    if report := _optional_model(state.get("report"), ReportArtifact):
        payload["report"] = report.json_data
    else:
        # The production read-only worker persists the same typed graph facts without
        # invoking the optional prose report node. Expose those facts as the public report
        # so the UI never has to reconstruct or invent a result.
        payload["report"] = {
            "incident_id": state.get("incident_id"),
            "status": IncidentStatus(state["status"]).value,
            "diagnosis": diagnosis.model_dump(mode="json") if diagnosis else None,
            "hypotheses": list(cast(list[dict[str, Any]], state.get("hypotheses", []))),
            "reports": list(cast(list[dict[str, Any]], state.get("reports", []))),
            "evidence_ids": list(cast(list[str], state.get("evidence_ids", []))),
            "tool_call_ids": list(cast(list[str], state.get("tool_call_ids", []))),
        }
    return payload


def _optional_model[ModelT: BaseModel](value: Any, model: type[ModelT]) -> ModelT | None:
    if value is None:
        return None
    return model.model_validate(value)


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:32]}"
