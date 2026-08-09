from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import EvidenceKind, ExecutionMode, IncidentStatus, Severity
from incidentpilot.incidents.models import (
    AlertRow,
    AnalysisJobRow,
    AuditEventRow,
    EvidenceRow,
    IncidentRow,
)
from incidentpilot.observability.redaction import redact_data
from incidentpilot.runtime.database import Database

ACTIVE_JOB_STATUSES = ("queued", "running", "retry")


class IncidentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    source: str
    external_id: str
    status: IncidentStatus
    severity: Severity
    title: str
    service: str | None
    created_at: datetime
    updated_at: datetime


class IncidentPage(BaseModel):
    items: list[IncidentView]
    next_cursor: str | None


class JobStart(BaseModel):
    job_id: str
    status: str
    created: bool


class AlertIngest(BaseModel):
    incident_id: str | None
    job_id: str | None
    created: bool


class EvidenceView(BaseModel):
    id: str
    incident_id: str
    kind: EvidenceKind
    source_system: str
    summary: str
    query: dict[str, Any]
    raw_json: dict[str, Any] | list[Any] | None = None
    source_uri: str | None
    observed_start: datetime
    observed_end: datetime
    truncated: bool
    collected_at: datetime


class TimelineEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    event_type: str
    actor_type: str
    actor_id: str
    payload: dict[str, Any]
    created_at: datetime


class IncidentService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_with_start_job(
        self,
        *,
        incident_id: str,
        tenant_id: str,
        alert: AlertPayload,
        job_id: str,
        available_at: datetime,
    ) -> None:
        async with self._database.session_factory() as session, session.begin():
            session.add(_incident_row(incident_id, tenant_id, alert))
            await session.flush()
            session.add(_alert_row(f"alert-{incident_id}", incident_id, alert, available_at))
            session.add(_job_row(job_id, incident_id, available_at))

    async def create_manual(
        self,
        *,
        tenant_id: str,
        title: str,
        description: str,
        severity: Severity,
        service: str,
        starts_at: datetime,
        start_analysis: bool = True,
        execution_mode: ExecutionMode = ExecutionMode.REVIEW,
    ) -> tuple[IncidentView, str | None]:
        incident_id = f"inc_{uuid4().hex}"
        job_id = f"job_{uuid4().hex}" if start_analysis else None
        alert = AlertPayload(
            external_id=incident_id,
            source="manual",
            title=title,
            description=description,
            severity=severity,
            starts_at=starts_at,
            service_hint=service,
            labels={"execution_mode": execution_mode.value},
        )
        if job_id is not None:
            await self.create_with_start_job(
                incident_id=incident_id,
                tenant_id=tenant_id,
                alert=alert,
                job_id=job_id,
                available_at=datetime.now(UTC),
            )
        else:
            async with self._database.session_factory() as session, session.begin():
                session.add(_incident_row(incident_id, tenant_id, alert))
                await session.flush()
                session.add(
                    _alert_row(f"alert-{incident_id}", incident_id, alert, datetime.now(UTC))
                )
        incident = await self.get_incident(tenant_id=tenant_id, incident_id=incident_id)
        if incident is None:
            raise RuntimeError("created incident could not be loaded")
        return incident, job_id

    async def ingest_alert_signal(
        self,
        *,
        tenant_id: str,
        alert: AlertPayload,
        signal_status: str,
    ) -> AlertIngest:
        lock_key = f"alert:{tenant_id}:{alert.source}:{alert.external_id}"
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            incident = (
                await session.scalars(
                    select(IncidentRow).where(
                        IncidentRow.tenant_id == tenant_id,
                        IncidentRow.source == alert.source,
                        IncidentRow.external_id == alert.external_id,
                    )
                )
            ).one_or_none()
            if incident is None and signal_status == "resolved":
                return AlertIngest(incident_id=None, job_id=None, created=False)

            created = incident is None
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            if incident is None:
                incident_id = f"inc_{uuid4().hex}"
                job_id = f"job_{uuid4().hex}"
                session.add(_incident_row(incident_id, tenant_id, alert))
                await session.flush()
                session.add(_job_row(job_id, incident_id, now))
            else:
                incident_id = incident.id
                job_id = await session.scalar(
                    select(AnalysisJobRow.id)
                    .where(AnalysisJobRow.incident_id == incident_id)
                    .order_by(AnalysisJobRow.available_at, AnalysisJobRow.id)
                    .limit(1)
                )

            signal_id = _stable_signal_id(incident_id, alert, signal_status)
            await session.execute(
                insert(AlertRow)
                .values(
                    id=signal_id,
                    incident_id=incident_id,
                    payload_json=alert.model_dump(mode="json"),
                    received_at=now,
                )
                .on_conflict_do_nothing(index_elements=[AlertRow.id])
            )
            return AlertIngest(incident_id=incident_id, job_id=job_id, created=created)

    async def ensure_start_job(self, *, tenant_id: str, incident_id: str) -> JobStart | None:
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"analysis:{incident_id}"},
            )
            incident = await session.scalar(
                select(IncidentRow.id).where(
                    IncidentRow.id == incident_id,
                    IncidentRow.tenant_id == tenant_id,
                )
            )
            if incident is None:
                return None
            active = (
                await session.scalars(
                    select(AnalysisJobRow)
                    .where(
                        AnalysisJobRow.incident_id == incident_id,
                        AnalysisJobRow.status.in_(ACTIVE_JOB_STATUSES),
                    )
                    .order_by(AnalysisJobRow.available_at, AnalysisJobRow.id)
                    .limit(1)
                )
            ).one_or_none()
            if active is not None:
                return JobStart(job_id=active.id, status=active.status, created=False)
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            job_id = f"job_{uuid4().hex}"
            session.add(_job_row(job_id, incident_id, now))
            return JobStart(job_id=job_id, status="queued", created=True)

    async def list_incidents(
        self,
        *,
        tenant_id: str,
        limit: int,
        cursor: str | None = None,
        severity: Severity | None = None,
        status: IncidentStatus | None = None,
        service: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> IncidentPage:
        cursor_value = _decode_cursor(cursor) if cursor else None
        if created_from is not None:
            _require_aware(created_from)
        if created_to is not None:
            _require_aware(created_to)
        if created_from is not None and created_to is not None and created_from > created_to:
            raise ValueError("created_from must not exceed created_to")
        service_query = _service_query()
        query = select(IncidentRow, service_query.label("service")).where(
            IncidentRow.tenant_id == tenant_id
        )
        if severity is not None:
            query = query.where(IncidentRow.severity == severity.value)
        if status is not None:
            query = query.where(IncidentRow.status == status.value)
        if service is not None:
            query = query.where(service_query == service)
        if created_from is not None:
            query = query.where(IncidentRow.created_at >= created_from)
        if created_to is not None:
            query = query.where(IncidentRow.created_at <= created_to)
        if cursor_value is not None:
            cursor_time, cursor_id = cursor_value
            query = query.where(
                or_(
                    IncidentRow.created_at < cursor_time,
                    and_(IncidentRow.created_at == cursor_time, IncidentRow.id < cursor_id),
                )
            )
        async with self._database.session_factory() as session:
            rows = (
                await session.execute(
                    query.order_by(IncidentRow.created_at.desc(), IncidentRow.id.desc()).limit(
                        limit + 1
                    )
                )
            ).all()
        items = [_incident_view(row, service_name) for row, service_name in rows[:limit]]
        next_cursor = _encode_cursor(items[-1]) if len(rows) > limit and items else None
        return IncidentPage(items=items, next_cursor=next_cursor)

    async def get_incident(self, *, tenant_id: str, incident_id: str) -> IncidentView | None:
        async with self._database.session_factory() as session:
            result = (
                await session.execute(
                    select(IncidentRow, _service_query().label("service")).where(
                        IncidentRow.id == incident_id,
                        IncidentRow.tenant_id == tenant_id,
                    )
                )
            ).one_or_none()
        return _incident_view(*result) if result else None

    async def list_evidence(self, *, tenant_id: str, incident_id: str) -> list[EvidenceView] | None:
        async with self._database.session_factory() as session:
            if not await _owns_incident(session, tenant_id, incident_id):
                return None
            rows = (
                await session.scalars(
                    select(EvidenceRow)
                    .where(EvidenceRow.incident_id == incident_id)
                    .order_by(EvidenceRow.collected_at, EvidenceRow.id)
                )
            ).all()
        return [_evidence_view(row, include_raw=False) for row in rows]

    async def get_evidence(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        evidence_id: str,
    ) -> EvidenceView | None:
        async with self._database.session_factory() as session:
            if not await _owns_incident(session, tenant_id, incident_id):
                return None
            row = await session.scalar(
                select(EvidenceRow).where(
                    EvidenceRow.id == evidence_id,
                    EvidenceRow.incident_id == incident_id,
                )
            )
        return _evidence_view(row, include_raw=True) if row else None

    async def list_timeline(
        self,
        *,
        tenant_id: str,
        incident_id: str,
    ) -> list[TimelineEventView] | None:
        async with self._database.session_factory() as session:
            if not await _owns_incident(session, tenant_id, incident_id):
                return None
            rows = (
                await session.scalars(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.tenant_id == tenant_id,
                        AuditEventRow.incident_id == incident_id,
                    )
                    .order_by(AuditEventRow.created_at, AuditEventRow.id)
                )
            ).all()
        return [
            TimelineEventView(
                id=row.id,
                event_type=row.event_type,
                actor_type=row.actor_type,
                actor_id=row.actor_id,
                payload=row.payload_json,
                created_at=row.created_at,
            )
            for row in rows
        ]


def _incident_row(incident_id: str, tenant_id: str, alert: AlertPayload) -> IncidentRow:
    return IncidentRow(
        id=incident_id,
        tenant_id=tenant_id,
        source=alert.source,
        external_id=alert.external_id,
        status=IncidentStatus.RECEIVED.value,
        severity=alert.severity.value,
        title=alert.title,
    )


def _alert_row(
    alert_id: str,
    incident_id: str,
    alert: AlertPayload,
    received_at: datetime,
) -> AlertRow:
    return AlertRow(
        id=alert_id,
        incident_id=incident_id,
        payload_json=alert.model_dump(mode="json"),
        received_at=received_at,
    )


def _job_row(job_id: str, incident_id: str, available_at: datetime) -> AnalysisJobRow:
    return AnalysisJobRow(
        id=job_id,
        incident_id=incident_id,
        job_type="START",
        resume_reference_id=None,
        status="queued",
        attempts=0,
        available_at=available_at,
    )


def _stable_signal_id(incident_id: str, alert: AlertPayload, signal_status: str) -> str:
    value = f"{incident_id}:{alert.starts_at.isoformat()}:{signal_status}"
    return f"alert_{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def _service_query():
    return (
        select(
            func.coalesce(
                AlertRow.payload_json["service_hint"].as_string(),
                AlertRow.payload_json["labels"]["service"].as_string(),
            )
        )
        .where(AlertRow.incident_id == IncidentRow.id)
        .order_by(AlertRow.received_at, AlertRow.id)
        .limit(1)
        .correlate(IncidentRow)
        .scalar_subquery()
    )


def _incident_view(row: IncidentRow, service: str | None) -> IncidentView:
    return IncidentView(
        id=row.id,
        tenant_id=row.tenant_id,
        source=row.source,
        external_id=row.external_id,
        status=IncidentStatus(row.status),
        severity=Severity(row.severity),
        title=row.title,
        service=service,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _evidence_view(row: EvidenceRow, *, include_raw: bool) -> EvidenceView:
    raw_json = cast(dict[str, Any] | list[Any] | None, redact_data(row.raw_json))
    return EvidenceView(
        id=row.id,
        incident_id=row.incident_id,
        kind=EvidenceKind(row.kind),
        source_system=row.source_system,
        summary=row.summary,
        query=row.query_json,
        raw_json=raw_json if include_raw else None,
        source_uri=row.source_uri,
        observed_start=row.observed_start,
        observed_end=row.observed_end,
        truncated=row.truncated,
        collected_at=row.collected_at,
    )


async def _owns_incident(session: AsyncSession, tenant_id: str, incident_id: str) -> bool:
    return (
        await session.scalar(
            select(IncidentRow.id).where(
                IncidentRow.id == incident_id,
                IncidentRow.tenant_id == tenant_id,
            )
        )
    ) is not None


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("time filters must be timezone-aware")


def _encode_cursor(incident: IncidentView) -> str:
    payload = json.dumps([incident.created_at.isoformat(), incident.id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        payload = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        decoded: object = json.loads(payload)
        if not isinstance(decoded, list):
            raise ValueError
        items = cast(list[object], decoded)
        if len(items) != 2 or not all(isinstance(item, str) for item in items):
            raise ValueError
        raw = cast(list[str], items)
        timestamp = datetime.fromisoformat(raw[0])
        _require_aware(timestamp)
        if not raw[1]:
            raise ValueError
        return timestamp, raw[1]
    except (
        binascii.Error,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid incident cursor") from exc
