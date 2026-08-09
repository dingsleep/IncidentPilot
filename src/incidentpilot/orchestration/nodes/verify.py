from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.actions import ActionProposal, VerificationResult
from incidentpilot.domain.enums import IncidentStatus


class RecoveryVerifier(Protocol):
    async def verify(
        self, *, incident_id: str, proposal: ActionProposal
    ) -> VerificationResult: ...


class VerifyNode:
    """M7.4 stays fail-safe; M7.5 supplies measured recovery verification."""

    def __init__(self, *, verifier: RecoveryVerifier) -> None:
        self._verifier = verifier

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        result = await self._verifier.verify(
            incident_id=str(state["incident_id"]),
            proposal=ActionProposal.model_validate(state["action_proposal"])
        )
        return {
            "verification_result": result.model_dump(mode="json"),
            "status": (
                IncidentStatus.RESOLVED.value
                if result.recovered
                else IncidentStatus.NEEDS_HUMAN.value
            ),
        }
