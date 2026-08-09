from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    RollbackChangeAction,
    VerificationCheck,
)
from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.diagnosis import Diagnosis, validate_diagnosis
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, RiskLevel
from incidentpilot.domain.events import DomainInvariantError, transition_status
from incidentpilot.domain.evidence import EvidenceRef

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def _evidence(evidence_id: str, incident_id: str, kind: EvidenceKind) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        incident_id=incident_id,
        kind=kind,
        source_system=kind.value,
        query={},
        observed_range=TimeRange(start=NOW, end=NOW),
        summary=f"{kind.value} signal",
        raw_digest_sha256="b" * 64,
        collected_at=NOW,
    )


def _diagnosis() -> Diagnosis:
    return Diagnosis(
        symptom_service="checkout",
        root_cause_service="checkout",
        dependency_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary="Payment calls fail",
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Orders fail",
    )


def _check() -> VerificationCheck:
    return VerificationCheck(
        service="checkout",
        metric="error_ratio",
        query_template_id="service_error_ratio",
        comparator="lt",
        threshold=0.02,
        observation_seconds=60,
    )


def test_diagnosis_requires_two_realtime_signal_kinds_from_same_incident() -> None:
    diagnosis = _diagnosis()
    metric = _evidence("ev-metric", "inc-1", EvidenceKind.METRIC)
    duplicate_kind = _evidence("ev-trace", "inc-1", EvidenceKind.METRIC)

    with pytest.raises(DomainInvariantError, match="two realtime"):
        validate_diagnosis(diagnosis, [metric, duplicate_kind], incident_id="inc-1")

    wrong_incident = _evidence("ev-trace", "inc-2", EvidenceKind.TRACE)
    with pytest.raises(DomainInvariantError, match="current incident"):
        validate_diagnosis(diagnosis, [metric, wrong_incident], incident_id="inc-1")

    trace = _evidence("ev-trace", "inc-1", EvidenceKind.TRACE)
    validate_diagnosis(diagnosis, [metric, trace], incident_id="inc-1")


def test_action_rejects_forged_compensation_and_illegal_risk() -> None:
    with pytest.raises(ValidationError, match="restart_service"):
        ActionProposal(
            action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
            risk=RiskLevel.LOW,
            diagnosis_evidence_ids=["ev-1", "ev-2"],
            expected_effect="Restore checkout",
            compensation_plan=CompensationPlan(
                mode="automatic_snapshot_restore",
                trigger="partial_execution_failure",
                reason="Forged snapshot semantics",
                snapshot_ref="snapshot-1",
            ),
            verification_checks=[_check()],
            idempotency_key="key-1",
        )

    with pytest.raises(ValidationError, match="rollback_change"):
        ActionProposal(
            action=RollbackChangeAction(target_service="checkout", change_id="chg-1"),
            risk=RiskLevel.MEDIUM,
            diagnosis_evidence_ids=["ev-1", "ev-2"],
            expected_effect="Restore configuration",
            compensation_plan=CompensationPlan(
                mode="not_applicable",
                trigger="none",
                reason="Missing rollback compensation",
            ),
            verification_checks=[_check()],
            idempotency_key="key-2",
        )

    payload: dict[str, Any] = {
        "action": {
            "action_type": "restart_service",
            "target_service": "checkout",
            "grace_period_seconds": 30,
        },
        "risk": "critical",
        "diagnosis_evidence_ids": ["ev-1", "ev-2"],
        "expected_effect": "Restore checkout",
        "compensation_plan": {
            "mode": "not_applicable",
            "trigger": "none",
            "reason": "No desired-state change",
        },
        "verification_checks": [_check().model_dump()],
        "idempotency_key": "key-3",
    }
    with pytest.raises(ValidationError, match="risk"):
        ActionProposal.model_validate(payload)


def test_incident_status_changes_only_through_explicit_transition_table() -> None:
    assert (
        transition_status(IncidentStatus.RECEIVED, IncidentStatus.TRIAGING)
        is IncidentStatus.TRIAGING
    )
    with pytest.raises(DomainInvariantError, match="illegal incident transition"):
        transition_status(IncidentStatus.RECEIVED, IncidentStatus.EXECUTING)

    with pytest.raises(TypeError, match="IncidentStatus"):
        transition_status("RECEIVED", "TRIAGING")
