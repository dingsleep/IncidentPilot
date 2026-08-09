from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.diagnosis import Diagnosis
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.telemetry.evidence_store import (
    EvidenceCandidate,
    EvidenceStore,
    EvidenceWrite,
    validate_diagnosis_references,
)

NOW = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)


class FakeEvidenceRepository:
    def __init__(self) -> None:
        self.by_identity: dict[tuple[str, EvidenceKind, str], EvidenceRef] = {}
        self.by_id: dict[str, EvidenceRef] = {}
        self.candidates: list[EvidenceCandidate] = []

    async def get_by_identity(
        self,
        incident_id: str,
        kind: EvidenceKind,
        digest: str,
    ) -> EvidenceRef | None:
        return self.by_identity.get((incident_id, kind, digest))

    async def add(self, candidate: EvidenceCandidate) -> EvidenceRef:
        self.candidates.append(candidate)
        reference = candidate.reference
        self.by_identity[(reference.incident_id, reference.kind, reference.raw_digest_sha256)] = (
            reference
        )
        self.by_id[reference.id] = reference
        return reference

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        return self.by_id.get(evidence_id)


def _ids() -> Callable[[], str]:
    values = iter(["ev-1", "ev-2", "ev-3"])
    return lambda: next(values)


def _write(kind: EvidenceKind, raw_json: dict[str, object]) -> EvidenceWrite:
    return EvidenceWrite(
        incident_id="inc-1",
        kind=kind,
        source_system="test",
        query={"template_id": "service_error_ratio"},
        raw_json=raw_json,
        observed_range=TimeRange(start=NOW, end=NOW),
        source_uri="http://127.0.0.1:3000/explore",
        truncated=True,
        collected_at=NOW,
    )


@pytest.mark.asyncio
async def test_evidence_store_redacts_hashes_summarizes_and_deduplicates() -> None:
    repository = FakeEvidenceRepository()
    store = EvidenceStore(repository=repository, id_factory=_ids())

    first = await store.persist(
        _write(
            EvidenceKind.METRIC,
            {"series": [1, 2], "authorization": "Bearer first"},
        )
    )
    duplicate = await store.persist(
        _write(
            EvidenceKind.METRIC,
            {"authorization": "Bearer different", "series": [1, 2]},
        )
    )
    other_kind = await store.persist(
        _write(
            EvidenceKind.LOG,
            {"series": [1, 2], "authorization": "Bearer first"},
        )
    )

    assert duplicate.id == first.id
    assert other_kind.id != first.id
    assert len(repository.candidates) == 2
    stored_raw = repository.candidates[0].raw_json
    assert isinstance(stored_raw, dict)
    assert stored_raw["authorization"] == "[REDACTED]"
    assert first.summary == "Metric evidence contains 2 series."
    assert first.source_uri == "http://127.0.0.1:3000/explore"
    assert first.truncated


@pytest.mark.asyncio
async def test_diagnosis_references_are_loaded_from_repository_and_must_exist() -> None:
    repository = FakeEvidenceRepository()
    store = EvidenceStore(repository=repository, id_factory=_ids())
    metric = await store.persist(_write(EvidenceKind.METRIC, {"series": [1]}))
    log = await store.persist(_write(EvidenceKind.LOG, {"records": [1]}))
    diagnosis = Diagnosis(
        symptom_service="checkout",
        root_cause_service="checkout",
        dependency_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary="Payment calls fail",
        confidence=0.9,
        evidence_ids=[metric.id, log.id],
        customer_impact="Orders fail",
    )

    await validate_diagnosis_references(
        diagnosis,
        incident_id="inc-1",
        repository=repository,
    )
    missing = diagnosis.model_copy(update={"evidence_ids": [metric.id, "ev-missing"]})
    with pytest.raises(DomainInvariantError, match="does not exist"):
        await validate_diagnosis_references(
            missing,
            incident_id="inc-1",
            repository=repository,
        )
