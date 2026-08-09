from __future__ import annotations

from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    RollbackChangeAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, RiskLevel
from incidentpilot.remediation.policy import ServerPolicyFacts, evaluate_pre_approval


def _proposal() -> ActionProposal:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-metric", "ev-trace"],
        expected_effect="Restore checkout availability.",
        compensation_plan=CompensationPlan(
            mode="not_applicable",
            trigger="none",
            reason="Restart does not change desired configuration.",
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


def _facts(**updates: object) -> ServerPolicyFacts:
    return ServerPolicyFacts(
        incident_status=IncidentStatus.PLANNING,
        actor_role="operator",
        known_evidence_ids={"ev-metric", "ev-trace"},
        available_realtime_evidence_kinds={
            EvidenceKind.METRIC,
            EvidenceKind.LOG,
            EvidenceKind.TRACE,
        },
        restart_allowlist={"checkout"},
        change_services={"chg-payment": "checkout"},
        verification_template_ids={"service_error_ratio"},
    ).model_copy(update=updates)


def test_policy_accepts_only_server_authorized_restart() -> None:
    decision = evaluate_pre_approval(_proposal(), _facts())

    assert decision.allowed
    assert decision.reason_codes == []
    assert decision.assigned_risk is RiskLevel.LOW


def test_policy_accumulates_stable_rejection_codes_from_server_facts() -> None:
    proposal = _proposal().model_copy(update={"risk": RiskLevel.MEDIUM})
    decision = evaluate_pre_approval(
        proposal,
        _facts(
            incident_status=IncidentStatus.DIAGNOSED,
            actor_role="viewer",
            known_evidence_ids={"ev-metric"},
            restart_allowlist=set[str](),
        ),
    )

    assert not decision.allowed
    assert decision.reason_codes == [
        "INCIDENT_NOT_PLANNING",
        "ACTOR_ROLE_DENIED",
        "TARGET_NOT_ALLOWLISTED",
        "RISK_MISMATCH",
        "EVIDENCE_NOT_FOUND",
    ]


def test_policy_requires_server_owned_change_and_verification_contract() -> None:
    proposal = ActionProposal(
        action=RollbackChangeAction(target_service="checkout", change_id="chg-other"),
        risk=RiskLevel.MEDIUM,
        diagnosis_evidence_ids=["ev-metric", "ev-trace"],
        expected_effect="Restore the change before the incident.",
        compensation_plan=CompensationPlan(
            mode="automatic_snapshot_restore",
            trigger="partial_execution_failure",
            reason="Restore the action-before flag snapshot after a partial failure.",
            snapshot_ref="snapshot-before-action",
        ),
        verification_checks=[
            VerificationCheck(
                service="payment",
                metric="error_ratio",
                query_template_id="unknown_template",
                comparator="lt",
                threshold=0.02,
                observation_seconds=60,
            )
        ],
        idempotency_key="rollback-checkout-inc-1",
    )

    decision = evaluate_pre_approval(proposal, _facts())

    assert not decision.allowed
    assert decision.reason_codes == [
        "CHANGE_NOT_OWNED_BY_TARGET",
        "VERIFICATION_TARGET_MISMATCH",
        "VERIFICATION_TEMPLATE_NOT_ALLOWED",
    ]
