from __future__ import annotations

import pytest

from incidentpilot.domain.diagnosis import (
    Diagnosis,
    InvestigationFinding,
    InvestigationReport,
)
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.orchestration.reducers import (
    keep_confirmed_diagnosis,
    merge_ids,
    merge_wave_reports,
)
from incidentpilot.orchestration.state import Investigator, WaveReport


def _report(*, wave: int, investigator: Investigator, finding: str) -> WaveReport:
    return WaveReport(
        wave=wave,
        report=InvestigationReport(
            investigator=investigator,
            scope_services=["checkout"],
            findings=[
                InvestigationFinding(
                    statement=finding,
                    evidence_ids=[f"ev-{wave}-{investigator}"],
                    signal_strength=0.8,
                )
            ],
        ),
    )


def _diagnosis(summary: str) -> Diagnosis:
    return Diagnosis(
        symptom_service="checkout",
        root_cause_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary=summary,
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Orders fail",
    )


def test_id_reducer_preserves_order_and_deduplicates_parallel_results() -> None:
    assert merge_ids(["ev-1", "ev-2"], ["ev-2", "ev-3", "ev-3"]) == [
        "ev-1",
        "ev-2",
        "ev-3",
    ]


def test_report_reducer_keeps_waves_and_rejects_conflicting_rewrite() -> None:
    first = _report(wave=1, investigator="metrics", finding="errors increased").model_dump(
        mode="json"
    )
    second = _report(wave=2, investigator="metrics", finding="errors persisted").model_dump(
        mode="json"
    )

    assert merge_wave_reports([first], [first, second]) == [first, second]

    conflicting = _report(wave=1, investigator="metrics", finding="different claim").model_dump(
        mode="json"
    )
    with pytest.raises(DomainInvariantError, match="cannot overwrite"):
        merge_wave_reports([first], [conflicting])


def test_confirmed_diagnosis_cannot_be_overwritten() -> None:
    confirmed = _diagnosis("Payment calls fail").model_dump(mode="json")

    assert keep_confirmed_diagnosis(None, confirmed) == confirmed
    assert keep_confirmed_diagnosis(confirmed, confirmed) == confirmed
    with pytest.raises(DomainInvariantError, match="cannot overwrite"):
        keep_confirmed_diagnosis(confirmed, _diagnosis("A different cause").model_dump(mode="json"))
