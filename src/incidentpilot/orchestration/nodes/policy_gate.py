from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.actions import ActionProposal
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.remediation.policy import (
    PolicyDecision,
    ServerPolicyFacts,
    evaluate_pre_approval,
)


class PolicyFactsLoader(Protocol):
    async def load_policy_facts(self, *, incident_id: str) -> ServerPolicyFacts: ...


class PolicyDecisionStore(Protocol):
    async def save_policy_decision(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        decision: PolicyDecision,
    ) -> None: ...


class PolicyGateNode:
    def __init__(self, *, facts: PolicyFactsLoader, decisions: PolicyDecisionStore) -> None:
        self._facts = facts
        self._decisions = decisions

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(state["incident_id"])
        proposal_id = str(state["action_proposal_id"])
        proposal = ActionProposal.model_validate(state["action_proposal"])
        decision = evaluate_pre_approval(
            proposal, await self._facts.load_policy_facts(incident_id=incident_id)
        )
        await self._decisions.save_policy_decision(
            incident_id=incident_id,
            proposal_id=proposal_id,
            decision=decision,
        )
        return {
            "policy_decision": decision.model_dump(mode="json"),
            "status": (
                IncidentStatus.WAITING_APPROVAL.value
                if decision.allowed
                else IncidentStatus.POLICY_REJECTED.value
            ),
            "terminal_reason": None if decision.allowed else ",".join(decision.reason_codes),
        }
