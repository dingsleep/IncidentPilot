from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.domain.alerts import AlertPayload, TimeRange
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.incidents.models import AlertRow, EvidenceRow, IncidentRow


class IncidentRepository(Protocol):
    async def create_incident(
        self,
        *,
        incident_id: str,
        tenant_id: str,
        alert: AlertPayload,
    ) -> None: ...

    async def get_incident_status(self, incident_id: str) -> IncidentStatus | None: ...

    async def add_evidence(self, evidence: EvidenceRef) -> None: ...

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None: ...


class SqlAlchemyIncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_incident(
        self,
        *,
        incident_id: str,
        tenant_id: str,
        alert: AlertPayload,
    ) -> None:
        incident = IncidentRow(
            id=incident_id,
            tenant_id=tenant_id,
            source=alert.source,
            external_id=alert.external_id,
            status=IncidentStatus.RECEIVED.value,
            severity=alert.severity.value,
            title=alert.title,
        )
        self._session.add(incident)
        await self._session.flush()
        self._session.add(
            AlertRow(
                id=f"alert-{incident_id}",
                incident_id=incident_id,
                payload_json=alert.model_dump(mode="json"),
                received_at=alert.starts_at,
            )
        )

    async def get_incident_status(self, incident_id: str) -> IncidentStatus | None:
        row = await self._session.get(IncidentRow, incident_id)
        return IncidentStatus(row.status) if row else None

    async def add_evidence(self, evidence: EvidenceRef) -> None:
        self._session.add(
            EvidenceRow(
                id=evidence.id,
                incident_id=evidence.incident_id,
                kind=evidence.kind.value,
                source_system=evidence.source_system,
                summary=evidence.summary,
                query_json=evidence.query,
                raw_json=None,
                digest=evidence.raw_digest_sha256,
                source_uri=evidence.source_uri,
                observed_start=evidence.observed_range.start,
                observed_end=evidence.observed_range.end,
                truncated=evidence.truncated,
                collected_at=evidence.collected_at,
            )
        )

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        row = await self._session.get(EvidenceRow, evidence_id)
        if row is None:
            return None
        return EvidenceRef(
            id=row.id,
            incident_id=row.incident_id,
            kind=EvidenceKind(row.kind),
            source_system=row.source_system,
            query=row.query_json,
            observed_range=TimeRange(start=row.observed_start, end=row.observed_end),
            summary=row.summary,
            source_uri=row.source_uri,
            raw_digest_sha256=row.digest,
            truncated=row.truncated,
            collected_at=row.collected_at,
        )
