from __future__ import annotations

from langgraph.types import Send

from incidentpilot.orchestration.state import (
    InvestigationBudget,
    InvestigationTask,
    TriageDecision,
    WaveReport,
)


class GraphRoutingError(RuntimeError):
    pass


def fan_out_investigators(
    decision: TriageDecision,
    budget: InvestigationBudget,
    *,
    incident_id: str,
) -> list[Send]:
    """Create bounded LangGraph sends from the validated triage decision."""
    tasks = [
        InvestigationTask(
            wave=budget.wave,
            investigator=investigator,
            scope_services=decision.scoped_services,
            objective=decision.objectives[investigator],
        )
        for investigator in decision.investigators
    ]
    return fan_out_tasks(tasks, incident_id=incident_id)


def fan_out_tasks(
    tasks: list[InvestigationTask],
    *,
    incident_id: str,
) -> list[Send]:
    return [
        Send(
            f"investigate_{task.investigator}",
            {
                "incident_id": incident_id,
                "task": task.model_dump(mode="json"),
            },
        )
        for task in tasks
    ]


def route_after_investigation(
    decision: TriageDecision,
    reports: list[WaveReport],
    *,
    wave: int,
) -> str:
    """Enforce a fan-in barrier before synthesis is scheduled."""
    completed = {item.report.investigator for item in reports if item.wave == wave}
    waiting = set(decision.investigators) - completed
    if waiting:
        raise GraphRoutingError(f"waiting for investigators: {sorted(waiting)}")
    return "synthesize"


def route_tasks_after_investigation(
    tasks: list[InvestigationTask],
    reports: list[WaveReport],
) -> str:
    if not tasks:
        raise GraphRoutingError("investigation wave has no tasks")
    waves = {task.wave for task in tasks}
    if len(waves) != 1:
        raise GraphRoutingError("investigation tasks must belong to one wave")
    wave = next(iter(waves))
    expected = {task.investigator for task in tasks}
    completed = {item.report.investigator for item in reports if item.wave == wave}
    waiting = expected - completed
    if waiting:
        raise GraphRoutingError(f"waiting for investigators: {sorted(waiting)}")
    return "synthesize"
