from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from incidentpilot.domain.enums import IncidentStatus


class AwaitApprovalNode:
    """Persist the LangGraph interrupt; the API later resumes it with an approval ID."""

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        approval_id = interrupt({"proposal_id": str(state["action_proposal_id"])})
        if not isinstance(approval_id, str) or not approval_id:
            raise ValueError("resume payload must be a non-empty approval reference")
        return {
            "approval_reference_id": approval_id,
            "status": IncidentStatus.AUTHORIZING.value,
        }
