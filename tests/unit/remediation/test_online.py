from datetime import UTC, datetime

from incidentpilot.domain.diagnosis import Diagnosis
from incidentpilot.domain.enums import RiskLevel
from incidentpilot.incidents.models import ChangeEventRow
from incidentpilot.remediation.online import build_rollback_proposal


def test_demo_rollback_proposal_is_evidence_bound_and_requires_approval() -> None:
    diagnosis = Diagnosis(
        symptom_service="checkout",
        root_cause_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary="payment name resolution failed",
        confidence=0.91,
        evidence_ids=["ev-metric", "ev-trace", "ev-log"],
        customer_impact="checkout requests fail",
    )
    change = ChangeEventRow(
        id="chg_demo_123",
        service="checkout",
        change_type="configuration",
        summary="Payment route changed",
        occurred_at=datetime.now(UTC),
    )

    proposal = build_rollback_proposal(
        incident_id="inc_123",
        diagnosis=diagnosis,
        change=change,
        baseline=0.24,
    )

    assert proposal.action.action_type == "rollback_change"
    assert proposal.action.change_id == change.id
    assert proposal.action.target_service == "checkout"
    assert proposal.risk is RiskLevel.MEDIUM
    assert proposal.diagnosis_evidence_ids == diagnosis.evidence_ids
    assert proposal.verification_checks[0].query_template_id == "service_error_ratio"
    assert proposal.verification_baseline == {"checkout:service_error_ratio:error_ratio": 0.24}

