import pytest
from pydantic import ValidationError

from incidentpilot.evaluation.metrics import RunAggregate
from incidentpilot.evolution.promotion_gate import ShadowEvaluation, evaluate_promotion


def _aggregate(*, score: float, root: float, cost: int, safety: int = 0) -> RunAggregate:
    return RunAggregate(
        mode="multi",
        case_count=4,
        weighted_score=score,
        root_cause_accuracy=root,
        evidence_fidelity=1.0,
        safety_hard_failures=safety,
        total_cost_microusd=cost,
        total_duration_ms=1000,
        total_tool_calls=12,
    )


def _evaluations() -> list[ShadowEvaluation]:
    return [
        ShadowEvaluation(
            candidate_id="candidate-123456789abc",
            split=split,
            seed=seed,
            environment_digest="a" * 64,
            model_profile="qwen3.7-flash",
            episode_order_digest=("b" if split == "train" else "c") * 64,
            active=_aggregate(score=0.80, root=0.80, cost=100),
            candidate=_aggregate(score=0.84, root=0.79, cost=90),
            historical_security_passed=True,
        )
        for split in ("train", "validation")
        for seed in (11, 12, 13)
    ]


def test_gate_recommends_staging_only_after_three_seed_train_validation_comparison() -> None:
    decision = evaluate_promotion(_evaluations())

    assert decision.recommendation == "staging"
    assert decision.validation_score_delta == pytest.approx(0.04)
    assert decision.worst_validation_score_delta == pytest.approx(0.04)
    assert all(check.passed for check in decision.checks)
    assert decision.active_write_requested is False


def test_gate_rejects_security_failure_and_execution_metadata_mismatch() -> None:
    evaluations = _evaluations()
    evaluations[0] = evaluations[0].model_copy(
        update={"candidate": _aggregate(score=0.90, root=0.90, cost=80, safety=1)}
    )
    evaluations[-1] = evaluations[-1].model_copy(update={"model_profile": "different-profile"})

    decision = evaluate_promotion(evaluations)

    assert decision.recommendation == "reject"
    assert {check.code for check in decision.checks if not check.passed} >= {
        "SAFETY_HARD_FAILURE",
        "EXECUTION_METADATA_MISMATCH",
    }


def test_holdout_is_rejected_before_gate_evaluation() -> None:
    with pytest.raises(ValidationError):
        ShadowEvaluation.model_validate(
            {
                "candidate_id": "candidate-123456789abc",
                "split": "holdout",
                "seed": 11,
                "environment_digest": "a" * 64,
                "model_profile": "qwen3.7-flash",
                "episode_order_digest": "b" * 64,
                "active": _aggregate(score=0.80, root=0.80, cost=100),
                "candidate": _aggregate(score=0.84, root=0.80, cost=90),
                "historical_security_passed": True,
            }
        )
