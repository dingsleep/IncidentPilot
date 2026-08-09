import pytest

from incidentpilot.evolution.candidate_generator import (
    generate_candidate,
    generate_candidates,
)
from incidentpilot.evolution.failure_mining import FailureCluster


def _cluster() -> FailureCluster:
    return FailureCluster(
        label="unsupported_claim",
        affected_component="synthesizer",
        reason_codes=["UNSUPPORTED_CLAIM"],
        episode_ids=["case-1"],
        representative_episode_id="case-1",
    )


def test_generates_only_the_three_supported_immutable_candidate_kinds() -> None:
    candidates = generate_candidates(
        cluster=_cluster(),
        base_version="prompt-v1",
        base_content="Use cited evidence.",
        target_agent="incident_commander",
        generator_model="qwen3.7-flash",
    )

    assert [candidate.kind for candidate in candidates] == [
        "prompt",
        "tool_description",
        "runbook_draft",
    ]
    assert all(candidate.status == "candidate" for candidate in candidates)
    assert all(candidate.base_version == "prompt-v1" for candidate in candidates)
    assert all(candidate.target_agent == "incident_commander" for candidate in candidates)
    assert all(candidate.target_failure_label == "unsupported_claim" for candidate in candidates)
    assert all(len(candidate.digest) == 64 for candidate in candidates)
    assert all("@@" in candidate.diff for candidate in candidates)
    assert candidates[0].model_config.get("frozen") is True


def test_rejects_unsupported_candidate_kind() -> None:
    with pytest.raises(ValueError, match="unsupported candidate kind"):
        generate_candidate(
            kind="model_weights",
            cluster=_cluster(),
            base_version="prompt-v1",
            base_content="Use cited evidence.",
            target_agent="incident_commander",
            generator_model="qwen3.7-flash",
        )
