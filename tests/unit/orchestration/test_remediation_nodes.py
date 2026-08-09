from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from incidentpilot.domain.actions import (
    ActionProposal,
    ActionResult,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.diagnosis import Diagnosis
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, RiskLevel
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.orchestration.graph import route_after_execution, route_after_policy_gate
from incidentpilot.orchestration.nodes.execute_action import ExecuteActionNode
from incidentpilot.orchestration.nodes.plan_remediation import PlanRemediationNode
from incidentpilot.orchestration.nodes.policy_gate import PolicyGateNode
from incidentpilot.orchestration.nodes.verify import VerifyNode
from incidentpilot.orchestration.state import IncidentGraphState
from incidentpilot.remediation.policy import PolicyDecision, ServerPolicyFacts
from incidentpilot.remediation.verification import verification_key


def _proposal(*, target: str) -> ActionProposal:
    return ActionProposal(
        action=RestartServiceAction(target_service=target, grace_period_seconds=10),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-1", "ev-2"],
        expected_effect="Restart the bounded target.",
        compensation_plan=CompensationPlan(
            mode="not_applicable",
            trigger="none",
            reason="Restart is bounded and has no config snapshot.",
        ),
        verification_checks=[
            VerificationCheck(
                service=target,
                metric="error_rate",
                query_template_id="service_error_rate",
                comparator="lt",
                threshold=0.01,
                observation_seconds=60,
            )
        ],
        idempotency_key="restart-payment-1",
    )


def _diagnosis() -> Diagnosis:
    return Diagnosis(
        symptom_service="checkout",
        root_cause_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary="Payment is unavailable.",
        confidence=0.9,
        evidence_ids=["ev-1", "ev-2"],
        customer_impact="Orders cannot complete.",
    )


@pytest.mark.asyncio
async def test_planner_cannot_persist_an_action_outside_allowed_catalog() -> None:
    class Catalog:
        async def list_allowed_actions(
            self, *, tenant_id: str, incident_id: str, target_service: str
        ) -> set[str]:
            assert tenant_id == "local"
            assert incident_id == "inc-1"
            assert target_service == "payment"
            return {"rollback_change"}

    class Planner:
        async def propose(
            self, *, diagnosis: Diagnosis, allowed_actions: set[str]
        ) -> ActionProposal:
            assert diagnosis.root_cause_service == "payment"
            assert allowed_actions == {"rollback_change"}
            return _proposal(target="payment")

    class Store:
        async def save_proposal(self, *, incident_id: str, proposal: ActionProposal) -> str:
            raise AssertionError("disallowed proposal must not be persisted")

    class Baselines:
        async def capture(self, *, proposal: ActionProposal) -> dict[str, float]:
            del proposal
            raise AssertionError("disallowed proposal must not capture a baseline")

    node = PlanRemediationNode(
        catalog=Catalog(), planner=Planner(), baselines=Baselines(), proposals=Store()
    )
    with pytest.raises(DomainInvariantError, match="outside the allowed catalog"):
        await node(
            {
                "incident_id": "inc-1",
                "tenant_id": "local",
                "diagnosis": _diagnosis().model_dump(mode="json"),
            }
        )


@pytest.mark.asyncio
async def test_planner_persists_a_complete_server_captured_verification_baseline() -> None:
    class Catalog:
        async def list_allowed_actions(
            self, *, tenant_id: str, incident_id: str, target_service: str
        ) -> set[str]:
            del tenant_id, incident_id, target_service
            return {"restart_service"}

    class Planner:
        async def propose(
            self, *, diagnosis: Diagnosis, allowed_actions: set[str]
        ) -> ActionProposal:
            del diagnosis, allowed_actions
            return _proposal(target="payment")

    class Baselines:
        async def capture(self, *, proposal: ActionProposal) -> dict[str, float]:
            check = proposal.verification_checks[0]
            return {f"{check.service}:{check.query_template_id}:{check.metric}": 0.8}

    captured: ActionProposal | None = None

    class Store:
        async def save_proposal(self, *, incident_id: str, proposal: ActionProposal) -> str:
            nonlocal captured
            assert incident_id == "inc-1"
            captured = proposal
            return "proposal-1"

    result = await PlanRemediationNode(
        catalog=Catalog(), planner=Planner(), baselines=Baselines(), proposals=Store()
    )(
        {
            "incident_id": "inc-1",
            "tenant_id": "local",
            "diagnosis": _diagnosis().model_dump(mode="json"),
        }
    )

    assert result["action_proposal_id"] == "proposal-1"
    assert captured is not None
    assert captured.verification_baseline == {"payment:service_error_rate:error_rate": 0.8}


@pytest.mark.asyncio
async def test_policy_rejection_terminates_without_returning_to_planner() -> None:
    stored: list[tuple[str, str, PolicyDecision]] = []

    class Facts:
        async def load_policy_facts(self, *, incident_id: str) -> ServerPolicyFacts:
            assert incident_id == "inc-1"
            return ServerPolicyFacts(
                incident_status=IncidentStatus.PLANNING,
                actor_role="viewer",
                known_evidence_ids={"ev-1", "ev-2"},
                available_realtime_evidence_kinds={
                    EvidenceKind.METRIC,
                    EvidenceKind.LOG,
                    EvidenceKind.TRACE,
                },
                restart_allowlist={"payment"},
                change_services={},
                verification_template_ids={"service_error_rate"},
            )

    class Decisions:
        async def save_policy_decision(
            self,
            *,
            incident_id: str,
            proposal_id: str,
            decision: PolicyDecision,
        ) -> None:
            stored.append((incident_id, proposal_id, decision))

    result = await PolicyGateNode(facts=Facts(), decisions=Decisions())(
        {
            "incident_id": "inc-1",
            "action_proposal_id": "proposal-1",
            "action_proposal": _proposal(target="payment").model_dump(mode="json"),
        }
    )

    assert result["status"] == IncidentStatus.POLICY_REJECTED.value
    assert result["terminal_reason"] == "ACTOR_ROLE_DENIED"
    assert route_after_policy_gate(cast(IncidentGraphState, result)) == "mark_reporting"
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_execute_node_passes_only_persisted_ids_and_typed_proposal_to_action_client() -> None:
    received: dict[str, object] = {}

    class Actions:
        async def execute(
            self,
            *,
            incident_id: str,
            proposal_id: str,
            proposal: ActionProposal,
            approval_id: str,
        ) -> ActionResult:
            received.update(
                incident_id=incident_id,
                proposal_id=proposal_id,
                proposal=proposal,
                approval_id=approval_id,
            )
            now = datetime(2026, 8, 1, tzinfo=UTC)
            return ActionResult(
                proposal_id=proposal_id,
                execution_id="exec-1",
                status="succeeded",
                started_at=now,
                finished_at=now,
            )

    result = await ExecuteActionNode(actions=Actions())(
        {
            "incident_id": "inc-1",
            "action_proposal_id": "proposal-1",
            "action_proposal": _proposal(target="payment").model_dump(mode="json"),
            "approval_reference_id": "approval-1",
        }
    )

    assert received["incident_id"] == "inc-1"
    assert received["proposal_id"] == "proposal-1"
    assert received["approval_id"] == "approval-1"
    assert result["status"] == IncidentStatus.EXECUTING.value


@pytest.mark.asyncio
async def test_verification_node_routes_a_failed_comparator_to_needs_human() -> None:
    proposal = _proposal(target="payment")
    check = proposal.verification_checks[0]
    proposal = proposal.model_copy(update={"verification_baseline": {verification_key(check): 0.8}})

    class Verifier:
        async def verify(self, *, incident_id: str, proposal: ActionProposal):
            assert incident_id == "inc-1"
            assert proposal.action.target_service == "payment"
            from incidentpilot.domain.actions import VerificationResult

            return VerificationResult(
                recovered=False,
                degraded=True,
                checks_passed=0,
                checks_total=1,
                evidence_ids=["ev-still-failing"],
                baseline={verification_key(check): 0.8},
                observed={verification_key(check): 0.8},
                explanation="The saved comparator did not pass.",
            )

    result = await VerifyNode(verifier=Verifier())(
        {"incident_id": "inc-1", "action_proposal": proposal.model_dump(mode="json")}
    )

    assert result["status"] == IncidentStatus.NEEDS_HUMAN.value
    assert result["verification_result"]["recovered"] is False


def test_failed_action_routes_to_needs_human_before_reporting() -> None:
    assert (
        route_after_execution(cast(IncidentGraphState, {"status": "ACTION_FAILED"}))
        == "mark_needs_human"
    )
