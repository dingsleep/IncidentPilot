from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from incidentpilot.evaluation.loader import (
    ScenarioLoadError,
    load_episode_suite,
    load_public_holdout_suite,
    verify_holdout_manifest,
)

ROOT = Path(__file__).parents[3]
SCENARIOS = ROOT / "scenarios"
CATALOG = ROOT / "service_catalog" / "otel-demo.yaml"


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _scenario(name: str) -> dict[str, Any]:
    return yaml.safe_load((SCENARIOS / name).read_text(encoding="utf-8"))


def test_loads_train_and_validation_as_separate_runtime_inputs() -> None:
    episodes = load_episode_suite(SCENARIOS, CATALOG)

    assert len(episodes) == 8
    assert {episode.split for episode in episodes} == {"train", "validation"}
    assert len({episode.id for episode in episodes}) == 8

    for episode in episodes:
        public_json = episode.public_input.model_dump_json()
        private_json = episode.execution.model_dump_json()
        assert episode.id not in public_json
        assert episode.family not in public_json
        assert "scenario_key" not in public_json
        assert "control_type" not in public_json
        assert "injections" not in public_json
        assert "ground_truth" not in public_json
        assert "expected_abstention" not in public_json
        assert "allowed_actions" not in public_json
        assert "recovery" not in public_json
        assert "cleanup" not in public_json
        assert "traffic" not in public_json
        assert all(
            injection.scenario_key not in public_json for injection in episode.execution.injections
        )
        assert "scenario_key" in private_json or episode.execution.control_type == "no_fault"

    payment = next(episode for episode in episodes if episode.id == "payment-failure-001")
    assert payment.execution.traffic is not None
    assert payment.execution.traffic.adapter == "otel-demo-http"
    assert payment.execution.traffic.operation == "checkout"
    assert payment.execution.traffic.requests == 6

    ad = next(episode for episode in episodes if episode.id == "ad-failure-001")
    assert ad.execution.traffic is not None
    assert ad.execution.traffic.operation == "ads"
    assert ad.execution.traffic.requests == 60

    llm = next(episode for episode in episodes if episode.id == "llm-rate-limit-001")
    assert llm.execution.traffic is not None
    assert llm.execution.traffic.operation == "ai_assistant"
    assert llm.execution.traffic.requests == 10

    product_catalog = next(
        episode for episode in episodes if episode.id == "product-catalog-failure-001"
    )
    assert product_catalog.execution.traffic is not None
    assert product_catalog.execution.traffic.operation == "product_detail"
    assert product_catalog.execution.traffic.requests == 2

    validation_traffic = {
        episode.id: episode.execution.traffic.operation
        for episode in episodes
        if episode.split == "validation" and episode.execution.traffic is not None
    }
    assert validation_traffic == {
        "cart-failure-001": "cart",
        "payment-unreachable-001": "checkout",
        "recommendation-cache-leak-001": "recommendations",
    }


def test_schema_enforces_fault_no_fault_and_distractor_injection_counts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scenarios"
    (root / "schema.json").parent.mkdir(parents=True)
    (root / "schema.json").write_text(
        (SCENARIOS / "schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    invalid = _scenario("train/payment-failure-001.yaml")
    invalid["injections"] = []
    _write_yaml(root / "train" / "invalid.yaml", invalid)

    with pytest.raises(ScenarioLoadError, match="injections"):
        load_episode_suite(root, CATALOG)

    invalid = _scenario("validation/no-fault-control-001.yaml")
    invalid["injections"] = [
        {
            "adapter": "flagd",
            "operation": "enable",
            "service": "payment",
            "scenario_key": "paymentFailure",
            "variant": "100%",
            "warmup_seconds": 30,
        }
    ]
    _write_yaml(root / "train" / "invalid.yaml", invalid)

    with pytest.raises(ScenarioLoadError, match="injections"):
        load_episode_suite(root, CATALOG)


def test_loader_rejects_duplicate_ids_cross_split_families_and_unknown_catalog_values(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scenarios"
    (root / "schema.json").parent.mkdir(parents=True)
    (root / "schema.json").write_text(
        (SCENARIOS / "schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    first = _scenario("train/payment-failure-001.yaml")
    second = _scenario("train/payment-failure-001.yaml")
    second["id"] = "payment-failure-validation-001"
    second["split"] = "validation"
    _write_yaml(root / "train" / "first.yaml", first)
    _write_yaml(root / "validation" / "second.yaml", second)

    with pytest.raises(ScenarioLoadError, match="family.*split"):
        load_episode_suite(root, CATALOG)

    second["family"] = "payment-failure-validation"
    second["id"] = first["id"]
    _write_yaml(root / "validation" / "second.yaml", second)
    with pytest.raises(ScenarioLoadError, match="duplicate scenario id"):
        load_episode_suite(root, CATALOG)

    second["id"] = "unknown-service-001"
    second["injections"][0]["service"] = "not-a-service"
    _write_yaml(root / "validation" / "second.yaml", second)
    with pytest.raises(ScenarioLoadError, match="unknown service"):
        load_episode_suite(root, CATALOG)

    second["injections"][0]["service"] = "payment"
    second["allowed_actions"] = ["arbitrary_shell"]
    _write_yaml(root / "validation" / "second.yaml", second)
    with pytest.raises(ScenarioLoadError, match="allowed_actions"):
        load_episode_suite(root, CATALOG)


def test_public_holdout_is_opaque_and_forbids_private_execution_fields() -> None:
    cases = load_public_holdout_suite(SCENARIOS, CATALOG)

    assert [case.case_id for case in cases] == [
        "case-h001",
        "case-h002",
        "case-h003",
        "case-h004",
    ]
    for case in cases:
        serialized = case.model_dump(mode="json")
        text = json.dumps(serialized, sort_keys=True)
        assert set(serialized) == {"schema_version", "case_id", "alert", "budgets"}
        assert not any(
            forbidden in text
            for forbidden in (
                "scenario_key",
                "ground_truth",
                "expected_abstention",
                "injections",
                "recovery",
                "cleanup",
            )
        )


def test_holdout_manifest_verifies_public_and_schema_digests(tmp_path: Path) -> None:
    manifest_path = SCENARIOS / "holdout" / "suite-manifest.json"
    digests = verify_holdout_manifest(manifest_path, ROOT)
    assert set(digests) == {"case-h001", "case-h002", "case-h003", "case-h004"}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["public_cases"][0]["sha256"] = "0" * 64
    tampered = tmp_path / "suite-manifest.json"
    tampered.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="manifest digest mismatch"):
        verify_holdout_manifest(tampered, ROOT)
