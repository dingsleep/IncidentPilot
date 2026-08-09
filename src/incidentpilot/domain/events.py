from __future__ import annotations

from typing import Any, TypedDict

from incidentpilot.domain.enums import IncidentStatus


class DomainInvariantError(ValueError):
    """Raised when valid domain values violate a cross-object invariant."""


ALLOWED_INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.RECEIVED: frozenset({IncidentStatus.TRIAGING}),
    IncidentStatus.TRIAGING: frozenset({IncidentStatus.INVESTIGATING}),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.SYNTHESIZING}),
    IncidentStatus.SYNTHESIZING: frozenset(
        {
            IncidentStatus.INVESTIGATING,
            IncidentStatus.DIAGNOSED,
            IncidentStatus.NEEDS_HUMAN,
        }
    ),
    IncidentStatus.DIAGNOSED: frozenset(
        {IncidentStatus.RESOLVED_READ_ONLY, IncidentStatus.PLANNING}
    ),
    IncidentStatus.PLANNING: frozenset(
        {IncidentStatus.POLICY_REJECTED, IncidentStatus.WAITING_APPROVAL}
    ),
    IncidentStatus.WAITING_APPROVAL: frozenset(
        {IncidentStatus.REJECTED, IncidentStatus.AUTHORIZING}
    ),
    IncidentStatus.AUTHORIZING: frozenset(
        {
            IncidentStatus.EXECUTING,
            IncidentStatus.WAITING_APPROVAL,
            IncidentStatus.NEEDS_HUMAN,
        }
    ),
    IncidentStatus.EXECUTING: frozenset(
        {
            IncidentStatus.VERIFYING,
            IncidentStatus.ROLLING_BACK,
            IncidentStatus.ACTION_FAILED,
        }
    ),
    IncidentStatus.VERIFYING: frozenset({IncidentStatus.RESOLVED, IncidentStatus.NEEDS_HUMAN}),
    IncidentStatus.ROLLING_BACK: frozenset(
        {IncidentStatus.ACTION_FAILED, IncidentStatus.NEEDS_HUMAN}
    ),
    IncidentStatus.RESOLVED: frozenset({IncidentStatus.REPORTING}),
    IncidentStatus.RESOLVED_READ_ONLY: frozenset({IncidentStatus.REPORTING}),
    IncidentStatus.REJECTED: frozenset({IncidentStatus.REPORTING}),
    IncidentStatus.POLICY_REJECTED: frozenset({IncidentStatus.REPORTING}),
    IncidentStatus.ACTION_FAILED: frozenset({IncidentStatus.REPORTING}),
    IncidentStatus.NEEDS_HUMAN: frozenset({IncidentStatus.REPORTING}),
    IncidentStatus.REPORTING: frozenset(),
}


def transition_status(
    current: object,
    target: object,
) -> IncidentStatus:
    if not isinstance(current, IncidentStatus) or not isinstance(target, IncidentStatus):
        raise TypeError("incident status transitions require IncidentStatus values")
    if target not in ALLOWED_INCIDENT_TRANSITIONS[current]:
        raise DomainInvariantError(f"illegal incident transition: {current} -> {target}")
    return target


class InvestigationBudget(TypedDict):
    wave: int
    max_waves: int
    read_calls_used: int
    max_read_calls: int


class IncidentGraphState(TypedDict, total=False):
    incident_id: str
    status: str
    alert: dict[str, Any]
    scoped_services: list[str]
    time_range: dict[str, str]
    investigation_budget: InvestigationBudget
    reports: list[dict[str, Any]]
    evidence_ids: list[str]
    hypotheses: list[dict[str, Any]]
    diagnosis: dict[str, Any] | None
    action_proposal: dict[str, Any] | None
    approval: dict[str, Any] | None
    action_result: dict[str, Any] | None
    verification: dict[str, Any] | None
    terminal_reason: str | None
    errors: list[dict[str, Any]]
