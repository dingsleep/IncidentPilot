from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select

from incidentpilot.domain import DomainModel
from incidentpilot.domain.diagnosis import Diagnosis, InvestigationFinding
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.incidents.models import (
    ActionExecutionRow,
    ActionProposalRow,
    ApprovalRow,
    DiagnosisRow,
    EvaluationCaseRow,
    EvaluationRunRow,
    EvidenceRow,
    ModelCallRow,
    ToolCallRow,
)
from incidentpilot.runtime.database import Database

EvaluationMode = Literal["baseline", "multi"]


class EvidenceFact(DomainModel):
    id: str
    incident_id: str
    kind: EvidenceKind
    summary: str
    raw_json: dict[str, Any] | list[Any]
    stored_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ToolCallFact(DomainModel):
    id: str
    tool_name: str
    args_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: str
    duration_ms: int = Field(ge=0)


class ModelCallFact(DomainModel):
    id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    status: str


class ActionFact(DomainModel):
    id: str
    action_type: str
    approved: bool
    policy_passed: bool
    authorization_passed: bool
    status: str


class EfficiencyBaseline(DomainModel):
    duration_ms: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    model_tokens: int = Field(ge=0)


class EvaluationFacts(DomainModel):
    case_id: str
    incident_id: str
    mode: EvaluationMode = "multi"
    seed: int = 0
    diagnosis_id: str | None = None
    diagnosis: Diagnosis | None = None
    abstained: bool = False
    findings: list[InvestigationFinding] = Field(
        default_factory=lambda: list[InvestigationFinding]()
    )
    evidence: list[EvidenceFact] = Field(default_factory=lambda: list[EvidenceFact]())
    tool_calls: list[ToolCallFact] = Field(default_factory=lambda: list[ToolCallFact]())
    model_calls: list[ModelCallFact] = Field(default_factory=lambda: list[ModelCallFact]())
    actions: list[ActionFact] = Field(default_factory=lambda: list[ActionFact]())
    duration_ms: int = Field(ge=0)
    recovery_passed: bool
    cleanup_succeeded: bool
    hidden_label_observed: bool = False
    policy_bypassed: bool = False
    authorization_bypassed: bool = False
    trajectory_uri: str | None = None
    service_aliases: dict[str, list[str]] = Field(default_factory=dict)


class ComponentScore(DomainModel):
    value: float = Field(ge=0, le=1)
    fact_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class CaseScore(DomainModel):
    scenario_id: str
    mode: EvaluationMode
    seed: int
    total: float = Field(ge=0, le=1)
    root_cause: ComponentScore
    root_cause_category: ComponentScore
    evidence_fidelity: ComponentScore
    signal_coverage: ComponentScore
    tool_process: ComponentScore
    safety: ComponentScore
    recovery: ComponentScore
    efficiency: ComponentScore
    hard_failures: list[str] = Field(default_factory=list)
    facts_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_call_count: int = Field(ge=0)
    model_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    trajectory_uri: str | None = None


class RunAggregate(DomainModel):
    mode: EvaluationMode
    case_count: int = Field(ge=1)
    weighted_score: float = Field(ge=0, le=1)
    root_cause_accuracy: float = Field(ge=0, le=1)
    evidence_fidelity: float = Field(ge=0, le=1)
    safety_hard_failures: int = Field(ge=0)
    total_cost_microusd: int = Field(ge=0)
    total_duration_ms: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)


class ModeComparison(DomainModel):
    weighted_score_delta: float
    root_cause_accuracy_delta: float
    evidence_fidelity_delta: float
    cost_microusd_delta: int
    duration_ms_delta: int
    tool_call_delta: int


def aggregate_run(*, mode: EvaluationMode, cases: list[CaseScore]) -> RunAggregate:
    selected = [case for case in cases if case.mode == mode]
    if not selected:
        raise ValueError(f"no {mode} cases to aggregate")
    count = len(selected)
    return RunAggregate(
        mode=mode,
        case_count=count,
        weighted_score=round(sum(case.total for case in selected) / count, 6),
        root_cause_accuracy=round(sum(case.root_cause.value for case in selected) / count, 6),
        evidence_fidelity=round(sum(case.evidence_fidelity.value for case in selected) / count, 6),
        safety_hard_failures=sum(bool(case.hard_failures) for case in selected),
        total_cost_microusd=sum(case.cost_microusd for case in selected),
        total_duration_ms=sum(case.duration_ms for case in selected),
        total_tool_calls=sum(case.tool_call_count for case in selected),
    )


def compare_modes(baseline: RunAggregate, multi: RunAggregate) -> ModeComparison:
    if baseline.mode != "baseline" or multi.mode != "multi":
        raise ValueError("comparison requires baseline then multi aggregates")
    return ModeComparison(
        weighted_score_delta=round(multi.weighted_score - baseline.weighted_score, 6),
        root_cause_accuracy_delta=round(
            multi.root_cause_accuracy - baseline.root_cause_accuracy, 6
        ),
        evidence_fidelity_delta=round(multi.evidence_fidelity - baseline.evidence_fidelity, 6),
        cost_microusd_delta=multi.total_cost_microusd - baseline.total_cost_microusd,
        duration_ms_delta=multi.total_duration_ms - baseline.total_duration_ms,
        tool_call_delta=multi.total_tool_calls - baseline.total_tool_calls,
    )


class EvaluationFactRepository:
    """Read the immutable database facts used by the deterministic scorer."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def load(
        self,
        *,
        case_id: str,
        incident_id: str,
        mode: EvaluationMode,
        seed: int,
        recovery_passed: bool,
        cleanup_succeeded: bool,
        trajectory_uri: str | None = None,
        service_aliases: dict[str, list[str]] | None = None,
    ) -> EvaluationFacts:
        async with self._database.session_factory() as session:
            diagnosis_row = await session.scalar(
                select(DiagnosisRow)
                .where(DiagnosisRow.incident_id == incident_id)
                .order_by(DiagnosisRow.id.desc())
                .limit(1)
            )
            evidence_rows = list(
                (
                    await session.scalars(
                        select(EvidenceRow)
                        .where(EvidenceRow.incident_id == incident_id)
                        .order_by(EvidenceRow.id)
                    )
                ).all()
            )
            tool_rows = list(
                (
                    await session.scalars(
                        select(ToolCallRow)
                        .where(ToolCallRow.incident_id == incident_id)
                        .order_by(ToolCallRow.id)
                    )
                ).all()
            )
            model_rows = list(
                (
                    await session.scalars(
                        select(ModelCallRow)
                        .where(ModelCallRow.incident_id == incident_id)
                        .order_by(ModelCallRow.id)
                    )
                ).all()
            )
            proposal_rows = list(
                (
                    await session.scalars(
                        select(ActionProposalRow).where(
                            ActionProposalRow.incident_id == incident_id
                        )
                    )
                ).all()
            )
            proposal_ids = [row.id for row in proposal_rows]
            approval_rows = (
                list(
                    (
                        await session.scalars(
                            select(ApprovalRow).where(ApprovalRow.proposal_id.in_(proposal_ids))
                        )
                    ).all()
                )
                if proposal_ids
                else []
            )
            execution_rows = (
                list(
                    (
                        await session.scalars(
                            select(ActionExecutionRow).where(
                                ActionExecutionRow.proposal_id.in_(proposal_ids)
                            )
                        )
                    ).all()
                )
                if proposal_ids
                else []
            )

        diagnosis = (
            Diagnosis.model_validate(diagnosis_row.payload_json)
            if diagnosis_row is not None
            else None
        )
        evidence = [
            EvidenceFact(
                id=row.id,
                incident_id=row.incident_id,
                kind=EvidenceKind(row.kind),
                summary=row.summary,
                raw_json=row.raw_json if row.raw_json is not None else {"unavailable": True},
                stored_digest=row.digest,
            )
            for row in evidence_rows
        ]
        tool_calls = [
            ToolCallFact(
                id=row.id,
                tool_name=row.tool_name,
                args_digest=row.args_digest,
                status=row.status,
                duration_ms=row.duration_ms,
            )
            for row in tool_rows
        ]
        model_calls = [
            ModelCallFact(
                id=row.id,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cost_microusd=row.cost_microusd,
                duration_ms=row.duration_ms,
                status=row.status,
            )
            for row in model_rows
        ]
        approvals_by_proposal: dict[str, list[ApprovalRow]] = {}
        for approval in approval_rows:
            approvals_by_proposal.setdefault(approval.proposal_id, []).append(approval)
        proposals_by_id = {row.id: row for row in proposal_rows}
        actions = [
            _action_fact(
                execution,
                proposals_by_id[execution.proposal_id],
                approvals_by_proposal.get(execution.proposal_id, []),
            )
            for execution in execution_rows
        ]
        return EvaluationFacts(
            case_id=case_id,
            incident_id=incident_id,
            mode=mode,
            seed=seed,
            diagnosis_id=diagnosis_row.id if diagnosis_row else None,
            diagnosis=diagnosis,
            abstained=diagnosis is None,
            evidence=evidence,
            tool_calls=tool_calls,
            model_calls=model_calls,
            actions=actions,
            duration_ms=sum(row.duration_ms for row in [*tool_rows, *model_rows]),
            recovery_passed=recovery_passed,
            cleanup_succeeded=cleanup_succeeded,
            trajectory_uri=trajectory_uri,
            service_aliases=service_aliases or {},
        )


class EvaluationResultStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_run(
        self,
        *,
        run_id: str,
        suite_version: str,
        candidate_version: str,
    ) -> None:
        async with self._database.session_factory() as session, session.begin():
            session.add(
                EvaluationRunRow(
                    id=run_id,
                    suite_version=suite_version,
                    candidate_version=candidate_version,
                    status="running",
                    aggregate_metrics={},
                )
            )

    async def add_case(self, *, run_id: str, score: CaseScore) -> None:
        identity = f"{run_id}\0{score.mode}\0{score.scenario_id}".encode()
        async with self._database.session_factory() as session, session.begin():
            session.add(
                EvaluationCaseRow(
                    id=f"ec-{hashlib.sha256(identity).hexdigest()[:40]}",
                    run_id=run_id,
                    scenario_id=score.scenario_id,
                    metrics=score.model_dump(mode="json"),
                    hard_failures=score.hard_failures,
                )
            )

    async def complete_run(self, *, run_id: str, aggregate: RunAggregate) -> None:
        async with self._database.session_factory() as session, session.begin():
            row = await session.get(EvaluationRunRow, run_id, with_for_update=True)
            if row is None:
                raise ValueError(f"evaluation run not found: {run_id}")
            row.status = "completed"
            row.aggregate_metrics = aggregate.model_dump(mode="json")

    async def fail_run(self, *, run_id: str, reason_code: str) -> None:
        async with self._database.session_factory() as session, session.begin():
            row = await session.get(EvaluationRunRow, run_id, with_for_update=True)
            if row is None:
                raise ValueError(f"evaluation run not found: {run_id}")
            row.status = "failed"
            row.aggregate_metrics = {"reason_code": reason_code}


def _action_fact(
    execution: ActionExecutionRow,
    proposal: ActionProposalRow,
    approvals: list[ApprovalRow],
) -> ActionFact:
    payload = proposal.payload_json
    policy = proposal.policy_result_json
    approved = [row for row in approvals if row.decision.upper() == "APPROVED"]
    return ActionFact(
        id=execution.id,
        action_type=str(payload.get("action_type", payload.get("type", "unknown"))),
        approved=bool(approved),
        policy_passed=bool(policy.get("allowed", policy.get("passed", False))),
        authorization_passed=any(
            row.grant_jws is not None and row.nonce_used_at is not None for row in approved
        ),
        status=execution.status,
    )
