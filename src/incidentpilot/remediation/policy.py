from __future__ import annotations

from incidentpilot.domain import DomainModel
from incidentpilot.domain.actions import ActionProposal, RestartServiceAction
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, RiskLevel

_APPROVER_ROLES = frozenset({"operator", "admin"})
_REQUIRED_REALTIME_KINDS = frozenset({EvidenceKind.METRIC, EvidenceKind.LOG, EvidenceKind.TRACE})


class ServerPolicyFacts(DomainModel):
    incident_status: IncidentStatus
    actor_role: str
    known_evidence_ids: set[str]
    available_realtime_evidence_kinds: set[EvidenceKind]
    restart_allowlist: set[str]
    change_services: dict[str, str]
    verification_template_ids: set[str]


class PolicyDecision(DomainModel):
    allowed: bool
    reason_codes: list[str]
    assigned_risk: RiskLevel


def evaluate_pre_approval(
    proposal: ActionProposal,
    facts: ServerPolicyFacts,
) -> PolicyDecision:
    """Evaluate only persisted server facts; proposal fields never grant authority."""
    reason_codes: list[str] = []
    assigned_risk = _assigned_risk(proposal)
    if facts.incident_status is not IncidentStatus.PLANNING:
        reason_codes.append("INCIDENT_NOT_PLANNING")
    if facts.actor_role not in _APPROVER_ROLES:
        reason_codes.append("ACTOR_ROLE_DENIED")
    if isinstance(proposal.action, RestartServiceAction):
        if proposal.action.target_service not in facts.restart_allowlist:
            reason_codes.append("TARGET_NOT_ALLOWLISTED")
    else:
        if facts.change_services.get(proposal.action.change_id) != proposal.action.target_service:
            reason_codes.append("CHANGE_NOT_OWNED_BY_TARGET")
    if proposal.risk is not assigned_risk:
        reason_codes.append("RISK_MISMATCH")
    if not set(proposal.diagnosis_evidence_ids) <= facts.known_evidence_ids:
        reason_codes.append("EVIDENCE_NOT_FOUND")
    if not facts.available_realtime_evidence_kinds >= _REQUIRED_REALTIME_KINDS:
        reason_codes.append("INSUFFICIENT_REALTIME_EVIDENCE")
    for check in proposal.verification_checks:
        if check.service != proposal.action.target_service:
            reason_codes.append("VERIFICATION_TARGET_MISMATCH")
            break
    for check in proposal.verification_checks:
        if check.query_template_id not in facts.verification_template_ids:
            reason_codes.append("VERIFICATION_TEMPLATE_NOT_ALLOWED")
            break
    return PolicyDecision(
        allowed=not reason_codes,
        reason_codes=reason_codes,
        assigned_risk=assigned_risk,
    )


def _assigned_risk(proposal: ActionProposal) -> RiskLevel:
    if isinstance(proposal.action, RestartServiceAction):
        return RiskLevel.LOW
    return RiskLevel.MEDIUM
