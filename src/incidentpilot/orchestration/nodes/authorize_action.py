from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.enums import IncidentStatus


class AuthorizationGate(Protocol):
    async def authorize(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        approval_id: str,
    ) -> None: ...


class AuthorizeActionNode:
    def __init__(self, *, gate: AuthorizationGate) -> None:
        self._gate = gate

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        await self._gate.authorize(
            incident_id=str(state["incident_id"]),
            proposal_id=str(state["action_proposal_id"]),
            approval_id=str(state["approval_reference_id"]),
        )
        return {"status": IncidentStatus.AUTHORIZING.value}
