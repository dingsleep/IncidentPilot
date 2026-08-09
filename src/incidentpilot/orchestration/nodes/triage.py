from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.orchestration.state import (
    PreparedContext,
    TriageDecision,
)


class TriageAgent(Protocol):
    async def triage(self, context: PreparedContext) -> TriageDecision: ...


class TriageNode:
    """Expose only the prepared value object to triage; no loader or backend is available."""

    def __init__(self, agent: TriageAgent) -> None:
        self._agent = agent

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        context = PreparedContext.model_validate(state["prepared_context"])
        decision = await self._agent.triage(context)
        known_services = {service.name for service in context.services}
        unknown = set(decision.scoped_services) - known_services
        if unknown:
            raise DomainInvariantError(f"triage selected unknown service: {sorted(unknown)[0]}")
        return {
            "triage": decision.model_dump(mode="json"),
            "scoped_services": decision.scoped_services,
            "status": IncidentStatus.INVESTIGATING.value,
        }
