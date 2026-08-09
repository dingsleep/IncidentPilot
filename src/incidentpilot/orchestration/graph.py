from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.domain.events import DomainInvariantError, transition_status
from incidentpilot.orchestration.routing import (
    fan_out_investigators,
    fan_out_tasks,
    route_tasks_after_investigation,
)
from incidentpilot.orchestration.state import (
    IncidentGraphState,
    InvestigationBudget,
    InvestigationTask,
    TriageDecision,
    WaveReport,
)

GraphNode = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_INVESTIGATION_NODES = (
    "investigate_metrics",
    "investigate_logs",
    "investigate_traces",
    "investigate_runbook",
)


@dataclass(frozen=True)
class ReadOnlyGraphNodes:
    prepare_context: GraphNode
    triage: GraphNode
    investigate_metrics: GraphNode
    investigate_logs: GraphNode
    investigate_traces: GraphNode
    investigate_runbooks: GraphNode
    synthesize: GraphNode
    report: GraphNode


@dataclass(frozen=True)
class RemediationGraphNodes:
    plan_remediation: GraphNode
    policy_gate: GraphNode
    await_approval: GraphNode
    authorize_action: GraphNode
    execute_action: GraphNode
    verify: GraphNode


def compile_read_only_graph(
    nodes: ReadOnlyGraphNodes,
    *,
    remediation: RemediationGraphNodes | None = None,
    checkpointer: Any = None,
    interrupt_after: Sequence[str] | None = None,
) -> Any:
    """Compile the bounded read-only workflow; runtime resources stay outside graph state."""
    builder: Any = StateGraph(IncidentGraphState)
    builder.add_node("mark_triaging", _mark_triaging)
    builder.add_node("prepare_context", nodes.prepare_context)
    builder.add_node("triage", nodes.triage)
    builder.add_node("dispatch", _prepare_dispatch)
    builder.add_node("investigate_metrics", nodes.investigate_metrics)
    builder.add_node("investigate_logs", nodes.investigate_logs)
    builder.add_node("investigate_traces", nodes.investigate_traces)
    builder.add_node("investigate_runbook", nodes.investigate_runbooks)
    builder.add_node("fan_in", _fan_in)
    builder.add_node("synthesize", nodes.synthesize)
    builder.add_node("resolve_read_only", _resolve_read_only)
    builder.add_node("mark_reporting", _mark_reporting)
    builder.add_node("report", nodes.report)
    if remediation is not None:
        builder.add_node("plan_remediation", remediation.plan_remediation)
        builder.add_node("policy_gate", remediation.policy_gate)
        builder.add_node("await_approval", remediation.await_approval)
        builder.add_node("authorize_action", remediation.authorize_action)
        builder.add_node("execute_action", remediation.execute_action)
        builder.add_node("mark_needs_human", _mark_needs_human)
        builder.add_node("mark_verifying", _mark_verifying)
        builder.add_node("verify", remediation.verify)

    builder.add_edge(START, "mark_triaging")
    builder.add_edge("mark_triaging", "prepare_context")
    builder.add_edge("prepare_context", "triage")
    builder.add_edge("triage", "dispatch")
    builder.add_conditional_edges("dispatch", _dispatch_routes, list(_INVESTIGATION_NODES))
    for node_name in _INVESTIGATION_NODES:
        builder.add_edge(node_name, "fan_in")
    builder.add_edge("fan_in", "synthesize")
    if remediation is None:
        builder.add_conditional_edges(
            "synthesize",
            _after_synthesis,
            {
                "dispatch": "dispatch",
                "resolve_read_only": "resolve_read_only",
                "mark_reporting": "mark_reporting",
            },
        )
    else:
        builder.add_conditional_edges(
            "synthesize",
            _after_synthesis_with_remediation,
            {
                "dispatch": "dispatch",
                "plan_remediation": "plan_remediation",
                "mark_reporting": "mark_reporting",
            },
        )
        builder.add_edge("plan_remediation", "policy_gate")
        builder.add_conditional_edges(
            "policy_gate",
            route_after_policy_gate,
            {
                "await_approval": "await_approval",
                "mark_reporting": "mark_reporting",
            },
        )
        builder.add_edge("await_approval", "authorize_action")
        builder.add_edge("authorize_action", "execute_action")
        builder.add_conditional_edges(
            "execute_action",
            route_after_execution,
            {
                "mark_verifying": "mark_verifying",
                "mark_needs_human": "mark_needs_human",
            },
        )
        builder.add_edge("mark_needs_human", "mark_reporting")
        builder.add_edge("mark_verifying", "verify")
        builder.add_edge("verify", "mark_reporting")
    builder.add_edge("resolve_read_only", "mark_reporting")
    builder.add_edge("mark_reporting", "report")
    builder.add_edge("report", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) if interrupt_after else None,
        name="incidentpilot-read-only",
    )


def graph_config(incident_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": incident_id}}


def _mark_triaging(state: IncidentGraphState) -> dict[str, Any]:
    return {"status": _transition(state, IncidentStatus.TRIAGING).value}


def _prepare_dispatch(state: IncidentGraphState) -> dict[str, Any]:
    pending = state.get("next_wave_tasks")
    if pending:
        tasks = [InvestigationTask.model_validate(item) for item in pending]
    else:
        raw_decision = state.get("triage")
        raw_budget = state.get("investigation_budget")
        if raw_decision is None or raw_budget is None:
            raise DomainInvariantError("dispatch requires triage and investigation budget")
        decision = TriageDecision.model_validate(raw_decision)
        budget = InvestigationBudget.model_validate(raw_budget)
        tasks = _tasks_from_triage(decision, budget)
    raw_budget = state.get("investigation_budget")
    if raw_budget is None:
        raise DomainInvariantError("dispatch requires investigation budget")
    budget = InvestigationBudget.model_validate(raw_budget)
    if any(task.wave != budget.wave for task in tasks):
        raise DomainInvariantError("dispatch task wave does not match investigation budget")
    return {
        "active_tasks": [task.model_dump(mode="json") for task in tasks],
        "next_wave_tasks": [],
    }


def _dispatch_routes(state: IncidentGraphState):
    incident_id = state.get("incident_id")
    raw_tasks = state.get("active_tasks")
    if not incident_id or not raw_tasks:
        raise DomainInvariantError("dispatch requires incident ID and active tasks")
    tasks = [InvestigationTask.model_validate(item) for item in raw_tasks]
    return fan_out_tasks(tasks, incident_id=incident_id)


def _fan_in(state: IncidentGraphState) -> dict[str, Any]:
    tasks = [InvestigationTask.model_validate(item) for item in state.get("active_tasks", [])]
    reports = [WaveReport.model_validate(item) for item in state.get("reports", [])]
    route_tasks_after_investigation(tasks, reports)
    raw_budget = state.get("investigation_budget")
    if raw_budget is None:
        raise DomainInvariantError("fan-in requires investigation budget")
    enforce_read_call_budget(
        state.get("tool_call_ids", []), InvestigationBudget.model_validate(raw_budget)
    )
    return {"status": _transition(state, IncidentStatus.SYNTHESIZING).value}


def enforce_read_call_budget(tool_call_ids: Sequence[str], budget: InvestigationBudget) -> None:
    if len(set(tool_call_ids)) > budget.max_read_calls:
        raise DomainInvariantError("investigation exceeded the global read-call budget")


def _after_synthesis(state: IncidentGraphState) -> str:
    status = _status(state)
    if status is IncidentStatus.INVESTIGATING:
        return "dispatch"
    if status is IncidentStatus.DIAGNOSED:
        return "resolve_read_only"
    if status is IncidentStatus.NEEDS_HUMAN:
        return "mark_reporting"
    raise DomainInvariantError(f"unexpected synthesis status: {status}")


def _after_synthesis_with_remediation(state: IncidentGraphState) -> str:
    status = _status(state)
    if status is IncidentStatus.INVESTIGATING:
        return "dispatch"
    if status is IncidentStatus.DIAGNOSED:
        return "plan_remediation"
    if status is IncidentStatus.NEEDS_HUMAN:
        return "mark_reporting"
    raise DomainInvariantError(f"unexpected synthesis status: {status}")


def route_after_policy_gate(state: IncidentGraphState) -> str:
    return (
        "await_approval" if _status(state) is IncidentStatus.WAITING_APPROVAL else "mark_reporting"
    )


def route_after_execution(state: IncidentGraphState) -> str:
    return "mark_verifying" if _status(state) is IncidentStatus.EXECUTING else "mark_needs_human"


def _resolve_read_only(state: IncidentGraphState) -> dict[str, Any]:
    return {"status": _transition(state, IncidentStatus.RESOLVED_READ_ONLY).value}


def _mark_reporting(state: IncidentGraphState) -> dict[str, Any]:
    return {"status": _transition(state, IncidentStatus.REPORTING).value}


def _mark_verifying(state: IncidentGraphState) -> dict[str, Any]:
    return {"status": _transition(state, IncidentStatus.VERIFYING).value}


def _mark_needs_human(state: IncidentGraphState) -> dict[str, Any]:
    return {"status": _transition(state, IncidentStatus.NEEDS_HUMAN).value}


def _tasks_from_triage(
    decision: TriageDecision,
    budget: InvestigationBudget,
) -> list[InvestigationTask]:
    sends = fan_out_investigators(decision, budget, incident_id="placeholder")
    return [InvestigationTask.model_validate(send.arg["task"]) for send in sends]


def _transition(state: IncidentGraphState, target: IncidentStatus) -> IncidentStatus:
    return transition_status(_status(state), target)


def _status(state: IncidentGraphState) -> IncidentStatus:
    value = state.get("status")
    if isinstance(value, IncidentStatus):
        return value
    return IncidentStatus(cast(str, value))
