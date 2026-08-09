from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import select

from incidentpilot.api.dependencies import get_runtime, require_role
from incidentpilot.api.errors import ApiProblem
from incidentpilot.domain import DomainModel
from incidentpilot.incidents.models import EvaluationCaseRow, EvaluationRunRow

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
EvaluationMode = Literal["baseline", "multi"]


class EvaluationComponentView(DomainModel):
    value: float = Field(ge=0, le=1)
    fact_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class EvaluationCaseMetricsView(DomainModel):
    scenario_id: str
    mode: EvaluationMode
    seed: int
    total: float = Field(ge=0, le=1)
    root_cause: EvaluationComponentView
    root_cause_category: EvaluationComponentView
    evidence_fidelity: EvaluationComponentView
    signal_coverage: EvaluationComponentView
    tool_process: EvaluationComponentView
    safety: EvaluationComponentView
    recovery: EvaluationComponentView
    efficiency: EvaluationComponentView
    hard_failures: list[str] = Field(default_factory=list)
    facts_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_call_count: int = Field(ge=0)
    model_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    trajectory_uri: str | None = None


class EvaluationAggregateView(DomainModel):
    mode: EvaluationMode | None = None
    case_count: int = Field(default=0, ge=0)
    weighted_score: float | None = Field(default=None, ge=0, le=1)
    root_cause_accuracy: float | None = Field(default=None, ge=0, le=1)
    evidence_fidelity: float | None = Field(default=None, ge=0, le=1)
    safety_hard_failures: int = Field(default=0, ge=0)
    total_cost_microusd: int = Field(default=0, ge=0)
    total_duration_ms: int = Field(default=0, ge=0)
    total_tool_calls: int = Field(default=0, ge=0)


class EvaluationRunView(DomainModel):
    id: str
    suite_version: str
    candidate_version: str
    status: str
    aggregate_metrics: EvaluationAggregateView


class EvaluationCaseView(DomainModel):
    id: str
    scenario_id: str
    metrics: EvaluationCaseMetricsView
    hard_failures: list[str]


class EvaluationRunDetail(EvaluationRunView):
    cases: list[EvaluationCaseView]


async def list_evaluation_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[EvaluationRunView]:
    require_role(request, "viewer")
    async with get_runtime(request).database.session_factory() as session:
        rows = (
            await session.scalars(
                select(EvaluationRunRow).order_by(EvaluationRunRow.id.desc()).limit(limit)
            )
        ).all()
    return [_run_view(row) for row in rows]


async def get_evaluation_run(run_id: str, request: Request) -> EvaluationRunDetail:
    require_role(request, "viewer")
    async with get_runtime(request).database.session_factory() as session:
        run = await session.get(EvaluationRunRow, run_id)
        if run is None:
            raise ApiProblem(
                status=404,
                code="EVALUATION_NOT_FOUND",
                title="Not Found",
                detail="The evaluation run was not found.",
            )
        cases = (
            await session.scalars(
                select(EvaluationCaseRow)
                .where(EvaluationCaseRow.run_id == run_id)
                .order_by(EvaluationCaseRow.scenario_id, EvaluationCaseRow.id)
            )
        ).all()
    view = _run_view(run)
    return EvaluationRunDetail(
        **view.model_dump(),
        cases=[_case_view(row) for row in cases],
    )


def _run_view(row: EvaluationRunRow) -> EvaluationRunView:
    allowed = EvaluationAggregateView.model_fields
    raw = row.aggregate_metrics
    aggregate = EvaluationAggregateView.model_validate(
        {key: raw[key] for key in allowed if key in raw}
    )
    return EvaluationRunView(
        id=row.id,
        suite_version=row.suite_version,
        candidate_version=row.candidate_version,
        status=row.status,
        aggregate_metrics=aggregate,
    )


def _case_view(row: EvaluationCaseRow) -> EvaluationCaseView:
    metrics = EvaluationCaseMetricsView.model_validate(row.metrics)
    failures = list(row.hard_failures)
    if metrics.scenario_id != row.scenario_id or metrics.hard_failures != failures:
        raise RuntimeError("evaluation case metrics do not match their database envelope")
    return EvaluationCaseView(
        id=row.id,
        scenario_id=row.scenario_id,
        metrics=metrics,
        hard_failures=failures,
    )


router.add_api_route("/runs", list_evaluation_runs, methods=["GET"])
router.add_api_route("/runs/{run_id}", get_evaluation_run, methods=["GET"])
