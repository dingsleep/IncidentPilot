from __future__ import annotations

import pytest

from incidentpilot.domain.diagnosis import InvestigationReport
from incidentpilot.orchestration.routing import (
    GraphRoutingError,
    fan_out_investigators,
    route_after_investigation,
)
from incidentpilot.orchestration.state import (
    InvestigationBudget,
    Investigator,
    TriageDecision,
    WaveReport,
)


def _decision() -> TriageDecision:
    return TriageDecision(
        scoped_services=["checkout", "payment"],
        investigators=["metrics", "logs", "traces"],
        objectives={
            "metrics": "Measure the error increase",
            "logs": "Find matching failures",
            "traces": "Locate the failing dependency",
        },
    )


def _report(investigator: Investigator) -> WaveReport:
    return WaveReport(
        wave=1,
        report=InvestigationReport(
            investigator=investigator,
            scope_services=["checkout"],
            findings=[],
        ),
    )


def test_triage_fans_out_to_selected_langgraph_nodes() -> None:
    sends = fan_out_investigators(
        _decision(),
        InvestigationBudget(wave=1, max_waves=2, read_calls_used=0, max_read_calls=12),
        incident_id="inc-1",
    )

    assert [send.node for send in sends] == [
        "investigate_metrics",
        "investigate_logs",
        "investigate_traces",
    ]
    assert {send.arg["incident_id"] for send in sends} == {"inc-1"}
    assert {send.arg["task"]["wave"] for send in sends} == {1}


def test_synthesize_route_is_a_fan_in_barrier() -> None:
    decision = _decision()
    with pytest.raises(GraphRoutingError, match="waiting for"):
        route_after_investigation(decision, [_report("metrics"), _report("logs")], wave=1)

    assert (
        route_after_investigation(
            decision,
            [_report("metrics"), _report("logs"), _report("traces")],
            wave=1,
        )
        == "synthesize"
    )
