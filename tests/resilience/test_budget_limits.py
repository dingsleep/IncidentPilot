from __future__ import annotations

import pytest
from pydantic import ValidationError

from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, RiskLevel
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.evaluation.loader import EpisodeBudgets
from incidentpilot.orchestration.graph import enforce_read_call_budget
from incidentpilot.orchestration.state import InvestigationBudget
from incidentpilot.remediation.policy import ServerPolicyFacts, evaluate_pre_approval


def _proposal() -> ActionProposal:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-metric", "ev-trace"],
        expected_effect="Restore checkout.",
        compensation_plan=CompensationPlan(mode="not_applicable", trigger="none", reason="restart"),
        verification_checks=[
            VerificationCheck(
                service="checkout", metric="error_ratio", query_template_id="service_error_ratio",
                comparator="lt", threshold=0.02, observation_seconds=60,
            )
        ],
        idempotency_key="resilience-budget-check",
    )


def test_episode_time_and_token_budget_rejects_out_of_range_configuration() -> None:
    with pytest.raises(ValidationError):
        EpisodeBudgets(max_duration_seconds=59, max_read_tool_calls=1, max_model_tokens=1_000)
    with pytest.raises(ValidationError):
        EpisodeBudgets(max_duration_seconds=60, max_read_tool_calls=1, max_model_tokens=200_001)


def test_global_read_call_budget_stops_fan_in() -> None:
    with pytest.raises(DomainInvariantError, match="global read-call budget"):
        enforce_read_call_budget(
            ["tc-1", "tc-2"],
            InvestigationBudget(wave=1, max_waves=2, read_calls_used=0, max_read_calls=1),
        )


def test_missing_one_realtime_signal_blocks_remediation_policy() -> None:
    decision = evaluate_pre_approval(
        _proposal(),
        ServerPolicyFacts(
            incident_status=IncidentStatus.PLANNING,
            actor_role="operator",
            known_evidence_ids={"ev-metric", "ev-trace"},
            available_realtime_evidence_kinds={EvidenceKind.METRIC, EvidenceKind.TRACE},
            restart_allowlist={"checkout"},
            change_services={},
            verification_template_ids={"service_error_ratio"},
        ),
    )

    assert not decision.allowed
    assert decision.reason_codes == ["INSUFFICIENT_REALTIME_EVIDENCE"]
