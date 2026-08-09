from pathlib import Path

from incidentpilot.evaluation.taxonomy import (
    TaxonomyFacts,
    classify_taxonomy,
    evaluate_taxonomy_suite,
    extract_taxonomy_facts,
    load_taxonomy_suite,
    verify_taxonomy_manifest,
)
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.orchestration.state import RcaDiagnosisDraft

ROOT = Path(__file__).parents[3]
SUITE_ROOT = ROOT / "scenarios" / "taxonomy"


def test_taxonomy_train_and_validation_are_disjoint_and_complete() -> None:
    assert verify_taxonomy_manifest(SUITE_ROOT / "manifest.json", ROOT) == "taxonomy-v8"
    train = load_taxonomy_suite(SUITE_ROOT / "train.yaml", expected_split="train")
    validation = load_taxonomy_suite(
        SUITE_ROOT / "validation.yaml",
        expected_split="validation",
    )

    assert len(train) == 15
    assert len(validation) == 10
    assert {case.id for case in train}.isdisjoint(case.id for case in validation)
    train_services = {
        service
        for case in train
        for service in (
            case.rca.symptom_service,
            case.rca.root_cause_service,
            case.rca.dependency_service,
        )
        if service is not None
    }
    validation_services = {
        service
        for case in validation
        for service in (
            case.rca.symptom_service,
            case.rca.root_cause_service,
            case.rca.dependency_service,
        )
        if service is not None
    }
    assert train_services.isdisjoint(validation_services)


def test_taxonomy_policy_passes_public_train_and_validation() -> None:
    train = load_taxonomy_suite(SUITE_ROOT / "train.yaml", expected_split="train")
    validation = load_taxonomy_suite(
        SUITE_ROOT / "validation.yaml",
        expected_split="validation",
    )

    assert evaluate_taxonomy_suite(train).accuracy == 1
    assert evaluate_taxonomy_suite(validation).accuracy == 1


def test_taxonomy_prefers_observed_cache_path_over_local_error_handling() -> None:
    rca = RcaDiagnosisDraft(
        symptom_service="feed-api",
        root_cause_service="feed-api",
        dependency_service="content-cache",
        root_cause_summary="The caller does not handle a cache-miss lookup error.",
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Feed requests fail.",
    )

    category = classify_taxonomy(
        rca,
        TaxonomyFacts(
            cache_hit_success=True,
            cache_miss_failure=True,
            failure_types=["not_found"],
        ),
    )

    assert category == "cache_failure"


def test_taxonomy_allows_cross_service_application_failure_without_dependency() -> None:
    rca = RcaDiagnosisDraft(
        symptom_service="frontend",
        root_cause_service="ad",
        dependency_service=None,
        root_cause_summary="Ad service has an internal failure.",
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Ads fail.",
    )

    assert classify_taxonomy(rca, TaxonomyFacts()) == "application_failure"


def test_taxonomy_prioritizes_declared_dependency_unreachable_over_root_marker() -> None:
    rca = RcaDiagnosisDraft(
        symptom_service="checkout-api",
        root_cause_service="checkout-api",
        dependency_service="billing-api",
        root_cause_summary="The caller cannot resolve its billing dependency.",
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Checkout fails.",
    )

    category = classify_taxonomy(
        rca,
        TaxonomyFacts(
            root_typed_failure_observed=True,
            failure_types=["name_resolution_error"],
        ),
    )

    assert category == "dependency_unreachable"


def test_extracts_bounded_taxonomy_facts_from_metric_and_trace_evidence() -> None:
    metrics = ToolEnvelope(
        ok=True,
        tool_call_id="tc-metric",
        evidence_id="ev-metric",
        data={
            "snapshots": {
                "content-cache": {
                    "container_memory_usage": {
                        "unit": "bytes",
                        "value": 4096.0,
                        "series_count": 1,
                        "truncated": False,
                    }
                }
            }
        },
    )
    traces = ToolEnvelope(
        ok=True,
        tool_call_id="tc-trace",
        evidence_id="ev-trace",
        data={
            "traces": [
                {
                    "error": False,
                    "observations": [
                        {"service": "feed-api", "attributes": {"app.cache_hit": True}}
                    ],
                    "error_spans": [],
                },
                {
                    "error": True,
                    "observations": [
                        {"service": "feed-api", "attributes": {"app.cache_hit": False}}
                    ],
                    "error_spans": [
                        {
                            "service": "content-cache",
                            "operation": "Get",
                            "status_code": "ERROR",
                            "failure_type": "not_found",
                        }
                    ],
                },
            ]
        },
    )

    facts = extract_taxonomy_facts({"metrics": metrics, "traces": traces})

    assert facts == TaxonomyFacts(
        cache_hit_success=True,
        cache_miss_failure=True,
        failure_types=["not_found"],
        observed_containers=["content-cache"],
    )

    unrelated = extract_taxonomy_facts(
        {"metrics": metrics, "traces": traces},
        service="other-api",
    )
    assert unrelated.cache_hit_success is False
    assert unrelated.cache_miss_failure is False
