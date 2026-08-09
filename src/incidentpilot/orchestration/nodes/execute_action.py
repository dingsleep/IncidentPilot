from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.actions import ActionProposal, ActionResult
from incidentpilot.domain.enums import IncidentStatus


class ApprovedActionClient(Protocol):
    async def execute(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        proposal: ActionProposal,
        approval_id: str,
    ) -> ActionResult: ...


class ExecuteActionNode:
    def __init__(self, *, actions: ApprovedActionClient) -> None:
        self._actions = actions

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        result = await self._actions.execute(
            incident_id=str(state["incident_id"]),
            proposal_id=str(state["action_proposal_id"]),
            proposal=ActionProposal.model_validate(state["action_proposal"]),
            approval_id=str(state["approval_reference_id"]),
        )
        return {
            "action_result": result.model_dump(mode="json"),
            "status": (
                IncidentStatus.EXECUTING.value
                if result.status in {"succeeded", "already_applied"}
                else IncidentStatus.ACTION_FAILED.value
            ),
        }
