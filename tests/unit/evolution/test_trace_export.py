import pytest

from incidentpilot.evolution.trace_export import (
    HoldoutTrajectoryError,
    ObservableMessage,
    ObservableToolCall,
    TrajectoryProvenance,
    TrajectorySplit,
    export_trajectory,
)


def _provenance(split: TrajectorySplit = "train") -> TrajectoryProvenance:
    return TrajectoryProvenance(
        run_id="run-1",
        scenario_id="case-train-1",
        seed=41,
        split=split,
        source="otel-demo",
        license="Apache-2.0",
    )


def test_export_redacts_sensitive_fields_and_has_stable_digest() -> None:
    exported = export_trajectory(
        provenance=_provenance(),
        messages=[ObservableMessage(role="user", content="Checkout fails")],
        tool_calls=[
            ObservableToolCall(
                name="search_traces",
                arguments={"service": "checkout", "token": "secret-token"},
                result={"traces": [], "approval_signature": "never-export"},
            )
        ],
        evidence=[{"id": "ev-1", "summary": "timeout", "private_flag_mapping": "hidden"}],
        diagnosis={"root_cause_service": "payment"},
        reward_components={"root_cause": 1.0},
        model_version="qwen3.7-flash",
        prompt_version="v1",
        tool_version="telemetry-v9",
    )

    assert exported.tool_calls[0].arguments["token"] == "[REDACTED]"
    assert isinstance(exported.tool_calls[0].result, dict)
    assert exported.tool_calls[0].result["approval_signature"] == "[REDACTED]"
    assert exported.evidence[0]["private_flag_mapping"] == "[REDACTED]"
    assert len(exported.digest) == 64


def test_export_rejects_holdout_before_serializing() -> None:
    with pytest.raises(HoldoutTrajectoryError):
        export_trajectory(
            provenance=_provenance("holdout"),
            messages=[],
            tool_calls=[],
            evidence=[],
            diagnosis=None,
            reward_components={},
            model_version="model",
            prompt_version="v1",
            tool_version="telemetry-v9",
        )
