from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import RiskLevel


class VerificationCheck(DomainModel):
    service: str
    metric: str
    query_template_id: str
    comparator: Literal["lt", "lte", "gt", "gte", "between"]
    threshold: float | list[float]
    observation_seconds: int = Field(ge=30, le=900)


class RestartServiceAction(DomainModel):
    action_type: Literal["restart_service"] = "restart_service"
    target_service: str
    grace_period_seconds: int = Field(ge=5, le=120)


class RollbackChangeAction(DomainModel):
    action_type: Literal["rollback_change"] = "rollback_change"
    target_service: str
    change_id: str


ActionIntent = Annotated[
    RestartServiceAction | RollbackChangeAction,
    Field(discriminator="action_type"),
]


class CompensationPlan(DomainModel):
    mode: Literal["automatic_snapshot_restore", "manual", "not_applicable"]
    trigger: Literal["partial_execution_failure", "verification_failure", "none"]
    reason: str = Field(min_length=1, max_length=500)
    snapshot_ref: str | None = None


class ActionProposal(DomainModel):
    action: ActionIntent
    risk: RiskLevel
    diagnosis_evidence_ids: list[str] = Field(min_length=2, max_length=20)
    expected_effect: str = Field(min_length=1, max_length=1000)
    compensation_plan: CompensationPlan
    verification_checks: list[VerificationCheck] = Field(min_length=1, max_length=8)
    verification_baseline: dict[str, float] = Field(default_factory=dict, max_length=8)
    idempotency_key: str

    @model_validator(mode="after")
    def enforce_compensation_semantics(self) -> Self:
        plan = self.compensation_plan
        if isinstance(self.action, RestartServiceAction):
            if plan.mode != "not_applicable" or plan.trigger != "none" or plan.snapshot_ref:
                raise ValueError(
                    "restart_service requires not_applicable compensation with no snapshot"
                )
        elif (
            plan.mode != "automatic_snapshot_restore"
            or plan.trigger != "partial_execution_failure"
            or not plan.snapshot_ref
        ):
            raise ValueError(
                "rollback_change requires an action-before snapshot restore only for "
                "partial execution failure"
            )
        return self


class ApprovalDecision(DomainModel):
    proposal_id: str
    decision: Literal["approve", "reject"]
    actor_id: str
    reason: str = Field(max_length=1000)
    decided_at: datetime


class ActionResult(DomainModel):
    proposal_id: str
    execution_id: str
    status: Literal["succeeded", "failed", "already_applied"]
    started_at: datetime
    finished_at: datetime
    external_reference: str | None = None
    sanitized_output: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(DomainModel):
    recovered: bool
    degraded: bool
    checks_passed: int
    checks_total: int
    evidence_ids: list[str]
    baseline: dict[str, float]
    observed: dict[str, float]
    explanation: str = Field(max_length=2000)
