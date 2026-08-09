from __future__ import annotations

from typing import Literal

from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef

_REALTIME_KINDS = frozenset(EvidenceKind) - {EvidenceKind.RUNBOOK}


class InvestigationFinding(DomainModel):
    statement: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    signal_strength: float = Field(ge=0.0, le=1.0)


class InvestigationReport(DomainModel):
    investigator: Literal["metrics", "logs", "traces", "runbook"]
    scope_services: list[str] = Field(min_length=1, max_length=20)
    findings: list[InvestigationFinding] = Field(max_length=20)
    contradictions: list[InvestigationFinding] = Field(
        default_factory=lambda: list[InvestigationFinding](), max_length=10
    )
    unanswered_questions: list[str] = Field(default_factory=list, max_length=10)
    tool_call_ids: list[str] = Field(default_factory=list, max_length=20)


class RootCauseHypothesis(DomainModel):
    id: str
    root_cause_service: str
    failure_mode: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_evidence: list[str] = Field(default_factory=list, max_length=10)
    falsification_checks: list[str] = Field(default_factory=list, max_length=10)


class Diagnosis(DomainModel):
    symptom_service: str
    root_cause_service: str
    dependency_service: str | None = None
    root_cause_category: str
    root_cause_summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=2, max_length=30)
    alternatives: list[RootCauseHypothesis] = Field(
        default_factory=lambda: list[RootCauseHypothesis](), max_length=2
    )
    customer_impact: str = Field(max_length=1000)
    diagnosis_limits: list[str] = Field(default_factory=list, max_length=10)

def validate_diagnosis(
    diagnosis: Diagnosis,
    evidence: list[EvidenceRef],
    *,
    incident_id: str,
) -> None:
    by_id = {item.id: item for item in evidence}
    try:
        referenced = [by_id[evidence_id] for evidence_id in diagnosis.evidence_ids]
    except KeyError as exc:
        raise DomainInvariantError(f"diagnosis references unknown evidence: {exc.args[0]}") from exc

    if any(item.incident_id != incident_id for item in referenced):
        raise DomainInvariantError("diagnosis evidence must belong to the current incident")

    realtime_kinds = {item.kind for item in referenced if item.kind in _REALTIME_KINDS}
    if len(realtime_kinds) < 2:
        raise DomainInvariantError("diagnosis requires at least two realtime evidence kinds")


def eligible_for_auto_planning(diagnosis: Diagnosis) -> bool:
    return diagnosis.confidence >= 0.75
