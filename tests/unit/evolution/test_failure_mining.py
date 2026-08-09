from incidentpilot.evolution.failure_mining import (
    FailureObservation,
    mine_failures,
    propose_improvements,
)


def test_mines_deterministic_labels_and_uses_stable_representative() -> None:
    clusters = mine_failures(
        [
            FailureObservation(
                episode_id="case-b",
                component="telemetry_tool",
                reason_codes=["TOOL_SELECTION", "INVALID_ARGS", "DUPLICATE_QUERY"],
            ),
            FailureObservation(
                episode_id="case-a",
                component="telemetry_tool",
                reason_codes=["INVALID_ARGS"],
            ),
            FailureObservation(
                episode_id="case-c",
                component="policy_gate",
                reason_codes=["UNAPPROVED_WRITE"],
            ),
        ]
    )

    assert [(cluster.label, cluster.episode_ids) for cluster in clusters] == [
        ("tool_selection", ["case-b"]),
        ("invalid_args", ["case-a", "case-b"]),
        ("duplicate_query", ["case-b"]),
        ("policy_rejection", ["case-c"]),
    ]
    assert clusters[1].representative_episode_id == "case-a"


def test_suggestions_are_bound_to_cluster_component_metric_and_risk() -> None:
    clusters = mine_failures(
        [
            FailureObservation(
                episode_id="case-1",
                component="synthesizer",
                reason_codes=["UNSUPPORTED_CLAIM"],
            )
        ]
    )

    suggestions = propose_improvements(clusters)

    assert len(suggestions) == 1
    assert suggestions[0].failure_label == "unsupported_claim"
    assert suggestions[0].affected_component == "synthesizer"
    assert suggestions[0].expected_metric == "evidence_fidelity"
    assert suggestions[0].regression_risk
    assert "whole prompt" not in suggestions[0].change.casefold()


def test_ignores_unknown_reason_codes_without_inventing_a_cluster() -> None:
    assert mine_failures(
        [
            FailureObservation(
                episode_id="case-1",
                component="worker",
                reason_codes=["UNKNOWN_FUTURE_CODE"],
            )
        ]
    ) == []
