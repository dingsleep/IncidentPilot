from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field

from incidentpilot.domain import DomainModel

FailureLabel = Literal[
    "tool_selection",
    "invalid_args",
    "duplicate_query",
    "missing_signal",
    "wrong_synthesis",
    "unsupported_claim",
    "policy_rejection",
    "no_recovery",
    "environment",
]

_LABEL_ORDER: tuple[FailureLabel, ...] = (
    "tool_selection",
    "invalid_args",
    "duplicate_query",
    "missing_signal",
    "wrong_synthesis",
    "unsupported_claim",
    "policy_rejection",
    "no_recovery",
    "environment",
)
_REASON_LABELS: dict[str, FailureLabel] = {
    "TOOL_SELECTION": "tool_selection",
    "TOOL_NOT_ALLOWED": "tool_selection",
    "TOOL_BUDGET_EXHAUSTED": "tool_selection",
    "INVALID_ARGS": "invalid_args",
    "INVALID_ARGUMENTS": "invalid_args",
    "INVALID_TOOL_ARGUMENTS": "invalid_args",
    "DUPLICATE_QUERY": "duplicate_query",
    "DUPLICATE_TOOL_CALL": "duplicate_query",
    "MISSING_SIGNAL": "missing_signal",
    "TOOL_RESULT_MISSING": "missing_signal",
    "INSUFFICIENT_EVIDENCE": "missing_signal",
    "WRONG_SYNTHESIS": "wrong_synthesis",
    "ROOT_CAUSE_MISMATCH": "wrong_synthesis",
    "WRONG_DIAGNOSIS": "wrong_synthesis",
    "UNSUPPORTED_CLAIM": "unsupported_claim",
    "FORGED_EVIDENCE_ID": "unsupported_claim",
    "EVIDENCE_DIGEST_MISMATCH": "unsupported_claim",
    "CROSS_INCIDENT_EVIDENCE": "unsupported_claim",
    "POLICY_REJECTION": "policy_rejection",
    "UNAPPROVED_WRITE": "policy_rejection",
    "NO_FAULT_WRITE": "policy_rejection",
    "POLICY_BYPASS": "policy_rejection",
    "AUTHORIZATION_BYPASS": "policy_rejection",
    "NO_RECOVERY": "no_recovery",
    "RECOVERY_FAILED": "no_recovery",
    "VERIFICATION_FAILED": "no_recovery",
    "ENVIRONMENT_CONTAMINATED": "environment",
    "CLEANUP_FAILED": "environment",
}


class FailureObservation(DomainModel):
    episode_id: str = Field(min_length=1, max_length=200)
    component: str = Field(min_length=1, max_length=100)
    reason_codes: list[str] = Field(min_length=1, max_length=30)


class FailureCluster(DomainModel):
    label: FailureLabel
    affected_component: str
    reason_codes: list[str]
    episode_ids: list[str]
    representative_episode_id: str


class ImprovementSuggestion(DomainModel):
    failure_label: FailureLabel
    affected_component: str
    representative_episode_id: str
    expected_metric: str
    regression_risk: str
    change: str


def mine_failures(observations: list[FailureObservation]) -> list[FailureCluster]:
    """Group only recognized public failure codes into stable, explainable clusters."""
    grouped: dict[tuple[FailureLabel, str], dict[str, set[str]]] = defaultdict(
        lambda: {"episodes": set(), "reasons": set()}
    )
    for observation in observations:
        for reason_code in observation.reason_codes:
            label = _REASON_LABELS.get(reason_code)
            if label is None:
                continue
            group = grouped[(label, observation.component)]
            group["episodes"].add(observation.episode_id)
            group["reasons"].add(reason_code)

    clusters = [
        FailureCluster(
            label=label,
            affected_component=component,
            reason_codes=sorted(group["reasons"]),
            episode_ids=sorted(group["episodes"]),
            representative_episode_id=min(group["episodes"]),
        )
        for (label, component), group in grouped.items()
    ]
    return sorted(
        clusters,
        key=lambda cluster: (_LABEL_ORDER.index(cluster.label), cluster.affected_component),
    )


def propose_improvements(clusters: list[FailureCluster]) -> list[ImprovementSuggestion]:
    """Create bounded, reviewable suggestions rather than editing a live artifact."""
    return [_suggestion(cluster) for cluster in clusters]


def _suggestion(cluster: FailureCluster) -> ImprovementSuggestion:
    metric, risk, change = _SUGGESTION_TEMPLATES[cluster.label]
    return ImprovementSuggestion(
        failure_label=cluster.label,
        affected_component=cluster.affected_component,
        representative_episode_id=cluster.representative_episode_id,
        expected_metric=metric,
        regression_risk=risk,
        change=change,
    )


_SUGGESTION_TEMPLATES: dict[FailureLabel, tuple[str, str, str]] = {
    "tool_selection": (
        "tool_validity",
        "Narrower routing can omit a useful read-only investigation.",
        "Refine the affected agent's tool description and routing examples for this task shape.",
    ),
    "invalid_args": (
        "tool_validity",
        "Stricter argument guidance can reject a previously accepted query form.",
        (
            "Add the failing parameter constraint and a valid example to the affected tool "
            "description."
        ),
    ),
    "duplicate_query": (
        "duplicate_call_rate",
        "Deduplication can suppress a query when its time window or filters genuinely changed.",
        "Clarify the affected agent's duplicate-query key using tool name, window, and filters.",
    ),
    "missing_signal": (
        "signal_coverage",
        "Requiring another signal can increase latency and tool budget consumption.",
        "Add an evidence-coverage checklist to the affected investigation prompt.",
    ),
    "wrong_synthesis": (
        "root_cause_accuracy",
        "More synthesis checks can increase abstentions on sparse incidents.",
        (
            "Add a contradiction check between the affected component's selected evidence and "
            "conclusion."
        ),
    ),
    "unsupported_claim": (
        "evidence_fidelity",
        "Citation requirements can make concise but valid diagnoses longer.",
        (
            "Require each material claim from the affected component to reference selected "
            "evidence IDs."
        ),
    ),
    "policy_rejection": (
        "policy_rejection_rate",
        "Precondition guidance can reduce legitimate proposals near a policy boundary.",
        (
            "Add the failed policy precondition to the affected proposal template before action "
            "planning."
        ),
    ),
    "no_recovery": (
        "recovery_success_rate",
        "Additional verification can delay a safe recovery recommendation.",
        "Add recovery verification evidence requirements to the affected runbook draft.",
    ),
    "environment": (
        "environment_cleanliness",
        "Extra reset checks increase evaluation duration.",
        (
            "Add an explicit reset and configuration-digest check to the affected evaluation "
            "component."
        ),
    ),
}
