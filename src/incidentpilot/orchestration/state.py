from __future__ import annotations

from typing import Annotated, Any, Literal, Self, TypedDict

from pydantic import Field, model_validator

from incidentpilot.domain import DomainModel
from incidentpilot.domain.diagnosis import (
    Diagnosis,
    InvestigationReport,
    RootCauseHypothesis,
)
from incidentpilot.orchestration.reducers import (
    keep_confirmed_diagnosis,
    merge_ids,
    merge_wave_reports,
)

Investigator = Literal["metrics", "logs", "traces", "runbook"]


class IncidentIdentity(DomainModel):
    incident_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)


class ServiceContext(DomainModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    owner: str = Field(min_length=1, max_length=200)
    criticality: str | None = Field(default=None, max_length=32)


class PreparedContext(DomainModel):
    incident_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    services: tuple[ServiceContext, ...] = Field(min_length=1, max_length=100)
    recent_change_evidence_ids: tuple[str, ...] = Field(max_length=40)


class TriageDecision(DomainModel):
    scoped_services: list[str] = Field(min_length=1, max_length=20)
    investigators: list[Investigator] = Field(min_length=1, max_length=4)
    objectives: dict[Investigator, str]

    @model_validator(mode="after")
    def investigators_are_unique_and_have_objectives(self) -> Self:
        if len(self.investigators) != len(set(self.investigators)):
            raise ValueError("investigators must be unique")
        if set(self.objectives) != set(self.investigators):
            raise ValueError("objectives must exactly match investigators")
        if any(not objective.strip() for objective in self.objectives.values()):
            raise ValueError("investigation objectives must not be blank")
        return self


class InvestigationBudget(DomainModel):
    wave: int = Field(ge=1)
    max_waves: int = Field(ge=1, le=10)
    read_calls_used: int = Field(ge=0)
    max_read_calls: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def usage_cannot_exceed_limits(self) -> Self:
        if self.wave > self.max_waves:
            raise ValueError("wave cannot exceed max_waves")
        if self.read_calls_used > self.max_read_calls:
            raise ValueError("read_calls_used cannot exceed max_read_calls")
        return self

    @property
    def can_continue(self) -> bool:
        return self.wave < self.max_waves and self.read_calls_used < self.max_read_calls


class InvestigationTask(DomainModel):
    wave: int = Field(ge=1)
    investigator: Investigator
    scope_services: list[str] = Field(min_length=1, max_length=20)
    objective: str = Field(min_length=1, max_length=1000)


class WaveReport(DomainModel):
    wave: int = Field(ge=1)
    report: InvestigationReport


class SynthesisDraft(DomainModel):
    hypotheses: list[RootCauseHypothesis] = Field(
        default_factory=lambda: list[RootCauseHypothesis](), max_length=3
    )
    diagnosis: Diagnosis | None = None
    next_wave_tasks: list[InvestigationTask] = Field(
        default_factory=lambda: list[InvestigationTask](), max_length=4
    )
    reason: str | None = Field(default=None, max_length=1000)


class RcaDiagnosisDraft(DomainModel):
    symptom_service: str
    root_cause_service: str
    dependency_service: str | None = None
    root_cause_summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    alternatives: list[RootCauseHypothesis] = Field(
        default_factory=lambda: list[RootCauseHypothesis](), max_length=2
    )
    customer_impact: str = Field(max_length=1000)
    diagnosis_limits: list[str] = Field(default_factory=list, max_length=10)


class RcaSynthesisDraft(DomainModel):
    hypotheses: list[RootCauseHypothesis] = Field(
        default_factory=lambda: list[RootCauseHypothesis](), max_length=3
    )
    diagnosis: RcaDiagnosisDraft | None = None
    next_wave_tasks: list[InvestigationTask] = Field(
        default_factory=lambda: list[InvestigationTask](), max_length=4
    )
    reason: str | None = Field(default=None, max_length=1000)


class ReportArtifact(DomainModel):
    markdown: str
    json_data: dict[str, Any]


class IncidentGraphState(TypedDict, total=False):
    incident_id: str
    tenant_id: str
    status: str
    alert: dict[str, Any]
    scoped_services: list[str]
    time_range: dict[str, Any]
    prepared_context: dict[str, Any]
    triage: dict[str, Any]
    investigation_budget: dict[str, Any]
    task: dict[str, Any]
    active_tasks: list[dict[str, Any]]
    next_wave_tasks: list[dict[str, Any]]
    reports: Annotated[list[dict[str, Any]], merge_wave_reports]
    evidence_ids: Annotated[list[str], merge_ids]
    tool_call_ids: Annotated[list[str], merge_ids]
    hypotheses: list[dict[str, Any]]
    diagnosis: Annotated[dict[str, Any] | None, keep_confirmed_diagnosis]
    action_proposal_id: str
    action_proposal: dict[str, Any]
    policy_decision: dict[str, Any]
    approval_reference_id: str
    action_result: dict[str, Any]
    verification_result: dict[str, Any]
    terminal_reason: str | None
    errors: list[dict[str, Any]]
    report: dict[str, Any]
