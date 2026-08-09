from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.domain import DomainModel
from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.diagnosis import Diagnosis, validate_diagnosis
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.incidents.models import EvidenceRow
from incidentpilot.observability.redaction import redact_data
from incidentpilot.telemetry.normalization import canonical_digest


class EvidenceWrite(DomainModel):
    incident_id: str
    kind: EvidenceKind
    source_system: str
    query: dict[str, Any]
    raw_json: dict[str, Any] | list[Any]
    observed_range: TimeRange
    source_uri: str | None = None
    truncated: bool = False
    collected_at: datetime


@dataclass(frozen=True)
class EvidenceCandidate:
    reference: EvidenceRef
    raw_json: dict[str, Any] | list[Any]


class EvidenceRepository(Protocol):
    async def get_by_identity(
        self,
        incident_id: str,
        kind: EvidenceKind,
        digest: str,
    ) -> EvidenceRef | None: ...

    async def add(self, candidate: EvidenceCandidate) -> EvidenceRef: ...

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None: ...


class EvidenceStore:
    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory or (lambda: f"ev_{uuid4().hex}")

    async def persist(self, write: EvidenceWrite) -> EvidenceRef:
        redacted = cast(dict[str, Any] | list[Any], redact_data(write.raw_json))
        digest = canonical_digest(redacted)
        existing = await self._repository.get_by_identity(
            write.incident_id,
            write.kind,
            digest,
        )
        if existing:
            return existing
        reference = EvidenceRef(
            id=self._id_factory(),
            incident_id=write.incident_id,
            kind=write.kind,
            source_system=write.source_system,
            query=write.query,
            observed_range=write.observed_range,
            summary=_summarize(write.kind, redacted),
            source_uri=write.source_uri,
            raw_digest_sha256=digest,
            truncated=write.truncated,
            collected_at=write.collected_at,
        )
        return await self._repository.add(EvidenceCandidate(reference=reference, raw_json=redacted))


class SqlAlchemyEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_identity(
        self,
        incident_id: str,
        kind: EvidenceKind,
        digest: str,
    ) -> EvidenceRef | None:
        row = (
            await self._session.scalars(
                select(EvidenceRow).where(
                    EvidenceRow.incident_id == incident_id,
                    EvidenceRow.kind == kind.value,
                    EvidenceRow.digest == digest,
                )
            )
        ).one_or_none()
        return _reference(row) if row else None

    async def add(self, candidate: EvidenceCandidate) -> EvidenceRef:
        reference = candidate.reference
        inserted_id = await self._session.scalar(
            insert(EvidenceRow)
            .values(
                id=reference.id,
                incident_id=reference.incident_id,
                kind=reference.kind.value,
                source_system=reference.source_system,
                summary=reference.summary,
                query_json=reference.query,
                raw_json=candidate.raw_json,
                digest=reference.raw_digest_sha256,
                source_uri=reference.source_uri,
                observed_start=reference.observed_range.start,
                observed_end=reference.observed_range.end,
                truncated=reference.truncated,
                collected_at=reference.collected_at,
            )
            .on_conflict_do_nothing(constraint="uq_evidence_identity")
            .returning(EvidenceRow.id)
        )
        if inserted_id:
            return reference
        existing = await self.get_by_identity(
            reference.incident_id,
            reference.kind,
            reference.raw_digest_sha256,
        )
        if existing is None:
            raise RuntimeError("evidence deduplication conflict could not be resolved")
        return existing

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        row = await self._session.get(EvidenceRow, evidence_id)
        return _reference(row) if row else None


async def validate_diagnosis_references(
    diagnosis: Diagnosis,
    *,
    incident_id: str,
    repository: EvidenceRepository,
) -> None:
    evidence: list[EvidenceRef] = []
    for evidence_id in diagnosis.evidence_ids:
        reference = await repository.get_evidence(evidence_id)
        if reference is None:
            raise DomainInvariantError(f"evidence does not exist: {evidence_id}")
        evidence.append(reference)
    validate_diagnosis(diagnosis, evidence, incident_id=incident_id)


def _summarize(
    kind: EvidenceKind,
    raw_json: dict[str, Any] | list[Any],
) -> str:
    key_by_kind = {
        EvidenceKind.METRIC: "series",
        EvidenceKind.LOG: "records",
        EvidenceKind.TRACE: "traces",
    }
    key = key_by_kind.get(kind)
    if key and isinstance(raw_json, dict) and isinstance(raw_json.get(key), list):
        count = len(cast(list[Any], raw_json[key]))
        return f"{kind.value.title()} evidence contains {count} {key}."
    return f"{kind.value.title()} evidence contains {len(raw_json)} top-level items."


def _reference(row: EvidenceRow) -> EvidenceRef:
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
