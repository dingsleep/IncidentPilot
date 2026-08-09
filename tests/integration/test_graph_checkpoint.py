from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from incidentpilot.bootstrap import open_checkpoint_saver
from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.diagnosis import Diagnosis, InvestigationReport
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.orchestration.graph import ReadOnlyGraphNodes, compile_read_only_graph
from incidentpilot.orchestration.nodes.report import ReportNode
from incidentpilot.orchestration.state import (
    IncidentGraphState,
    InvestigationBudget,
    Investigator,
    PreparedContext,
    ServiceContext,
    TriageDecision,
    WaveReport,
)
from incidentpilot.runtime.job_queue import ClaimedJob
from incidentpilot.worker.main import GraphJobHandler

CHECKPOINT_URL = (
    "postgresql://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
    "?options=-csearch_path%3Dlanggraph_checkpoint"
)
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _nodes(calls: dict[str, int]) -> ReadOnlyGraphNodes:
    async def prepare(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "prepared_context": PreparedContext(
                incident_id=state["incident_id"],
                tenant_id=state["tenant_id"],
                services=(
                    ServiceContext(
                        name="checkout", dependencies=["payment"], owner="checkout-team"
                    ),
                    ServiceContext(name="payment", dependencies=[], owner="payment-team"),
                ),
                recent_change_evidence_ids=(),
            ).model_dump(mode="json")
        }

    async def triage(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "triage": TriageDecision(
                scoped_services=["checkout", "payment"],
                investigators=["metrics", "logs"],
                objectives={"metrics": "Measure errors", "logs": "Find payment errors"},
            ).model_dump(mode="json"),
            "scoped_services": ["checkout", "payment"],
            "status": IncidentStatus.INVESTIGATING.value,
        }

    def investigate(
        name: Investigator,
        evidence_id: str,
    ) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def run(state: dict[str, Any]) -> dict[str, Any]:
            calls[name] += 1
            task = state["task"]
            return {
                "reports": [
                    WaveReport(
                        wave=task["wave"],
                        report=InvestigationReport(
                            investigator=name,
                            scope_services=task["scope_services"],
                            findings=[],
                        ),
                    ).model_dump(mode="json")
                ],
                "evidence_ids": [evidence_id],
                "tool_call_ids": [f"tc-{name}"],
            }

        return run

    async def synthesize(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "diagnosis": Diagnosis(
                symptom_service="checkout",
                root_cause_service="payment",
                root_cause_category="dependency_failure",
                root_cause_summary="Payment calls fail",
                confidence=0.9,
                evidence_ids=["ev-metric", "ev-log"],
                customer_impact="Orders fail",
            ).model_dump(mode="json"),
            "hypotheses": [],
            "status": IncidentStatus.DIAGNOSED.value,
        }

    async def unused(state: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"unexpected investigator: {state['task']}")

    return ReadOnlyGraphNodes(
        prepare_context=prepare,
        triage=triage,
        investigate_metrics=investigate("metrics", "ev-metric"),
        investigate_logs=investigate("logs", "ev-log"),
        investigate_traces=unused,
        investigate_runbooks=unused,
        synthesize=synthesize,
        report=ReportNode(),
    )


@pytest.mark.integration
async def test_graph_resumes_after_parallel_investigation_without_repeating_nodes() -> None:
    thread_id = f"inc-checkpoint-{uuid4().hex}"
    calls = {"metrics": 0, "logs": 0}
    config = {"configurable": {"thread_id": thread_id}}
    initial: IncidentGraphState = {
        "incident_id": thread_id,
        "tenant_id": "local",
        "status": IncidentStatus.RECEIVED.value,
        "scoped_services": ["checkout"],
        "time_range": TimeRange(start=NOW, end=NOW).model_dump(mode="json"),
        "investigation_budget": InvestigationBudget(
            wave=1,
            max_waves=2,
            read_calls_used=0,
            max_read_calls=10,
        ).model_dump(mode="json"),
        "reports": [],
        "evidence_ids": [],
        "tool_call_ids": [],
        "hypotheses": [],
        "diagnosis": None,
        "errors": [],
    }

    async with open_checkpoint_saver(CHECKPOINT_URL, setup=True) as saver:
        interrupted = compile_read_only_graph(
            _nodes(calls),
            checkpointer=saver,
            interrupt_after=["fan_in"],
        )
        await interrupted.ainvoke(initial, config)
        snapshot = await interrupted.aget_state(config)
        assert snapshot.next == ("synthesize",)
        assert calls == {"metrics": 1, "logs": 1}

        resumed = compile_read_only_graph(_nodes(calls), checkpointer=saver)

        class UnusedInitialState:
            async def load(self, incident_id: str) -> dict[str, Any]:
                raise AssertionError(f"checkpoint should be resumed: {incident_id}")

        class CapturingSink:
            state: dict[str, Any] | None = None

            async def persist(self, incident_id: str, state: dict[str, Any]) -> None:
                assert incident_id == thread_id
                self.state = state

        sink = CapturingSink()
        await GraphJobHandler(
            graph=resumed,
            initial_state=UnusedInitialState(),
            result_sink=sink,
        )(
            ClaimedJob(
                id=f"job-{uuid4().hex}",
                incident_id=thread_id,
                job_type="START",
                resume_reference_id=None,
                attempts=2,
            )
        )
        assert sink.state is not None
        result = sink.state

    assert result["status"] == IncidentStatus.REPORTING.value
    assert result["report"]["json_data"]["diagnosis"]["root_cause_service"] == "payment"
    json.dumps(result, allow_nan=False)
    assert calls == {"metrics": 1, "logs": 1}
