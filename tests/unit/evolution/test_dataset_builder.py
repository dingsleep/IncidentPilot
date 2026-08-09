from incidentpilot.evolution.dataset_builder import build_dataset
from incidentpilot.evolution.trace_export import (
    ObservableMessage,
    TrajectoryProvenance,
    export_trajectory,
)


def _trajectory(*, scenario_id: str, quality: dict[str, bool] | None = None):
    return export_trajectory(
        provenance=TrajectoryProvenance(
            run_id="run-1",
            scenario_id=scenario_id,
            seed=41,
            split="train",
            source="otel-demo",
            license="Apache-2.0",
        ),
        messages=[ObservableMessage(role="user", content="Checkout fails")],
        tool_calls=[],
        evidence=[{"id": "ev-1"}],
        diagnosis={"root_cause_service": "payment"},
        reward_components={"root_cause": 1.0},
        model_version="model",
        prompt_version="v1",
        tool_version="telemetry-v9",
        quality=quality,
    )


def test_dataset_builder_filters_invalid_trajectories_and_deduplicates() -> None:
    accepted = _trajectory(scenario_id="case-a")
    duplicate = _trajectory(scenario_id="case-b")
    rejected = _trajectory(scenario_id="case-c", quality={"environment_clean": False})

    dataset = build_dataset([accepted, duplicate, rejected])

    assert [item.provenance.scenario_id for item in dataset.samples] == ["case-a"]
    assert dataset.rejections["case-b"] == ["DUPLICATE_TRAJECTORY"]
    assert dataset.rejections["case-c"] == ["ENVIRONMENT_CONTAMINATED"]
