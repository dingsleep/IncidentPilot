from __future__ import annotations

import math
from typing import Any, Protocol

from incidentpilot.domain.actions import ActionProposal
from incidentpilot.domain.diagnosis import Diagnosis
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.remediation.verification import verification_key


class AllowedActionCatalog(Protocol):
    async def list_allowed_actions(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        target_service: str,
    ) -> set[str]: ...


class RemediationPlanner(Protocol):
    async def propose(
        self,
        *,
        diagnosis: Diagnosis,
        allowed_actions: set[str],
    ) -> ActionProposal: ...


class VerificationBaselineCollector(Protocol):
    async def capture(self, *, proposal: ActionProposal) -> dict[str, float]: ...


class ProposalStore(Protocol):
    async def save_proposal(self, *, incident_id: str, proposal: ActionProposal) -> str: ...


class PlanRemediationNode:
    """Create one typed proposal from the server-provided action catalog only."""

    def __init__(
        self,
        *,
        catalog: AllowedActionCatalog,
        planner: RemediationPlanner,
        baselines: VerificationBaselineCollector,
        proposals: ProposalStore,
    ) -> None:
        self._catalog = catalog
        self._planner = planner
        self._baselines = baselines
        self._proposals = proposals

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(state["incident_id"])
        tenant_id = str(state["tenant_id"])
        diagnosis = Diagnosis.model_validate(state["diagnosis"])
        allowed_actions = await self._catalog.list_allowed_actions(
            tenant_id=tenant_id,
            incident_id=incident_id,
            target_service=diagnosis.root_cause_service,
        )
        proposal = await self._planner.propose(
            diagnosis=diagnosis,
            allowed_actions=allowed_actions,
        )
        if proposal.action.action_type not in allowed_actions:
            raise DomainInvariantError("planner selected an action outside the allowed catalog")
        baseline = await self._baselines.capture(proposal=proposal)
        expected_keys = {verification_key(check) for check in proposal.verification_checks}
        if set(baseline) != expected_keys or not all(
            math.isfinite(value) for value in baseline.values()
        ):
            raise DomainInvariantError(
                "verification baseline must cover every check with finite values"
            )
        proposal = proposal.model_copy(update={"verification_baseline": baseline})
        proposal_id = await self._proposals.save_proposal(
            incident_id=incident_id,
            proposal=proposal,
        )
        return {
            "action_proposal_id": proposal_id,
            "action_proposal": proposal.model_dump(mode="json"),
            "status": IncidentStatus.PLANNING.value,
        }
