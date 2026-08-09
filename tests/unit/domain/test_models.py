from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.alerts import AlertPayload, TimeRange
from incidentpilot.domain.diagnosis import (
    Diagnosis,
    InvestigationFinding,
    InvestigationReport,
    RootCauseHypothesis,
)
from incidentpilot.domain.enums import EvidenceKind, RiskLevel, Severity
from incidentpilot.domain.evidence import EvidenceRef

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def test_domain_models_forbid_extra_fields_and_require_aware_time() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AlertPayload.model_validate(
            {
                "external_id": "alert-1",
                "source": "prometheus",
                "title": "Checkout errors",
                "description": "",
                "severity": Severity.P1,
                "starts_at": NOW,
                "unexpected": True,
            }
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        TimeRange(start=datetime(2026, 7, 16, 9, 0), end=NOW)


def test_core_models_accept_a_valid_read_only_diagnosis_and_action() -> None:
    observed_range = TimeRange(start=NOW, end=NOW)
    evidence = EvidenceRef(
        id="ev-1",
        incident_id="inc-1",
        kind=EvidenceKind.METRIC,
        source_system="prometheus",
        query={"template_id": "service_error_ratio"},
        observed_range=observed_range,
        summary="Checkout error ratio is 100%",
        raw_digest_sha256="a" * 64,
        collected_at=NOW,
    )
    report = InvestigationReport(
        investigator="metrics",
        scope_services=["checkout"],
        findings=[
            InvestigationFinding(
                statement="Checkout errors increased",
                evidence_ids=[evidence.id],
                signal_strength=1,
            )
        ],
    )
    diagnosis = Diagnosis(
        symptom_service="checkout",
        root_cause_service="checkout",
        dependency_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary="Checkout cannot charge through payment",
        confidence=0.9,
        evidence_ids=["ev-1", "ev-2"],
        alternatives=[
            RootCauseHypothesis(
                id="hyp-1",
                root_cause_service="payment",
                failure_mode="Payment internal failure",
                confidence=0.1,
                supporting_evidence_ids=["ev-2"],
            )
        ],
        customer_impact="Orders fail",
    )
    proposal = ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=diagnosis.evidence_ids,
        expected_effect="Restore checkout availability",
        compensation_plan=CompensationPlan(
            mode="not_applicable",
            trigger="none",
            reason="Restart does not change desired configuration",
        ),
        verification_checks=[
            VerificationCheck(
                service="checkout",
                metric="error_ratio",
                query_template_id="service_error_ratio",
                comparator="lt",
                threshold=0.02,
                observation_seconds=60,
            )
        ],
        idempotency_key="restart-checkout-inc-1",
    )

    assert report.investigator == "metrics"
    assert diagnosis.root_cause_service == "checkout"
    assert proposal.action.action_type == "restart_service"


def test_diagnosis_allows_cross_service_application_failure_without_dependency() -> None:
    diagnosis = Diagnosis(
        symptom_service="frontend",
        root_cause_service="ad",
        dependency_service=None,
        root_cause_category="application_failure",
        root_cause_summary="Ad service has an internal failure.",
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Ads fail.",
    )

    assert diagnosis.root_cause_category == "application_failure"
