from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from incidentpilot.config import LlmSettings, ModelSettings
from incidentpilot.domain.diagnosis import RootCauseHypothesis
from incidentpilot.evaluation.cli import (
    EVALUATION_LOG_SEVERITIES,
    EVALUATION_METRIC_WINDOW_MINUTES,
    EVALUATION_SYNTHESIS_VERSION,
    bind_correlated_log_evidence,
    build_candidate_version,
    build_evaluation_profile,
    build_evidence_alignment_context,
    build_parser,
    build_recovery_query,
    build_service_dependency_context,
    build_suite_version,
    drive_otel_demo_traffic,
    effective_recovery_observation_seconds,
    price_model_call,
    recovery_observation_schedule,
    select_episodes,
    synthesize_with_taxonomy,
)
from incidentpilot.evaluation.loader import TrafficSpec, load_episode_suite
from incidentpilot.llm.usage import ModelCallRecord, ModelUsage
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.orchestration.prompts import load_prompt_set
from incidentpilot.orchestration.state import (
    RcaDiagnosisDraft,
    RcaSynthesisDraft,
)

ROOT = Path(__file__).resolve().parents[3]


def test_cli_supports_required_m63_filters() -> None:
    args = build_parser().parse_args(
        [
            "--mode",
            "multi",
            "--split",
            "validation",
            "--scenario",
            "payment-unreachable-001",
            "--seed",
            "11",
            "--model-profile",
            "fast",
            "--structured-output-strategy",
            "json_output",
            "--no-actions",
        ]
    )

    assert args.mode == "multi"
    assert args.split == "validation"
    assert args.scenario == "payment-unreachable-001"
    assert args.seed == 11
    assert args.model_profile == "fast"
    assert args.structured_output_strategy == "json_output"
    assert args.no_actions is True


def test_evaluation_log_query_keeps_real_info_activity_in_the_episode_window() -> None:
    assert EVALUATION_LOG_SEVERITIES == ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")
    assert EVALUATION_METRIC_WINDOW_MINUTES == 2
    assert EVALUATION_SYNTHESIS_VERSION == "v15-a1-t8-m1"


def test_cli_keeps_tool_strategy_as_the_default() -> None:
    args = build_parser().parse_args(["--mode", "multi", "--split", "train", "--no-actions"])

    assert args.structured_output_strategy == "tool_strategy"


def test_suite_version_tracks_the_train_traffic_contract() -> None:
    assert build_suite_version("train") == "train-v3-score-v5"
    assert build_suite_version("validation") == "validation-v2-score-v5"


def test_candidate_version_includes_strategy_query_and_prompt_digests() -> None:
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    version = build_candidate_version(
        profile,
        "json_output",
        query_digest="a" * 12,
        prompt_digest="b" * 12,
        schema_version="v2",
        tool_version="telemetry-v3",
    )

    assert version == (
        "p1-bbbbbbbbbbbb:deepseek-v4-flash:json_output:q-aaaaaaaaaaaa:t-telemetry-v3:s-v2"
    )
    assert len(version) <= 100


def test_current_candidate_version_fits_the_database_contract() -> None:
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    version = build_candidate_version(
        profile,
        "json_output",
        query_digest="a" * 12,
        prompt_digest="b" * 12,
        schema_version=EVALUATION_SYNTHESIS_VERSION,
        tool_version="telemetry-v9",
    )

    assert len(version) <= 100


def test_service_dependency_context_preserves_only_scoped_catalog_edges() -> None:
    context = build_service_dependency_context(
        ["checkout", "payment"],
        {
            "checkout": {"dependencies": ["cart", "payment"]},
            "payment": {"dependencies": ["flagd"]},
        },
    )

    assert context == {"checkout": ["payment"], "payment": []}


def test_evidence_alignment_does_not_treat_unrelated_success_logs_as_contradiction() -> None:
    context = build_evidence_alignment_context(
        {
            "logs": ToolEnvelope(
                ok=True,
                tool_call_id="tc-log",
                evidence_id="ev-log",
                data={
                    "records": [
                        {
                            "severity": "INFO",
                            "body": "Item found",
                            "trace_id": "trace-healthy",
                        },
                        {
                            "severity": "ERROR",
                            "body": "Lookup failed",
                            "trace_id": "trace-error",
                        },
                    ]
                },
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={
                    "traces": [
                        {
                            "trace_id": "trace-error",
                            "error": True,
                            "error_spans": [
                                {
                                    "service": "catalog-api",
                                    "operation": "GetItem",
                                    "status_code": "ERROR",
                                }
                            ],
                        },
                        {
                            "trace_id": "trace-healthy",
                            "error": False,
                            "error_spans": [],
                        },
                    ]
                },
            ),
        }
    )

    assert context == {
        "error_trace_ids": ["trace-error"],
        "failing_operations": [
            {
                "service": "catalog-api",
                "operation": "GetItem",
                "error_trace_count": 1,
            }
        ],
        "log_alignment": {
            "record_count": 2,
            "correlated_error_trace_records": 1,
            "uncorrelated_success_records": 1,
        },
    }


@pytest.mark.asyncio
async def test_multi_synthesis_classifies_taxonomy_from_compact_rca_facts() -> None:
    rca = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="checkout",
            root_cause_service="payment",
            dependency_service="payment",
            root_cause_summary="Payment Charge spans return errors to checkout.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Checkout requests fail.",
        )
    )
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = rca
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-1",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"alert":{},"reports":[{"large":"payload"}]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True,
                tool_call_id="tc-metric",
                evidence_id="ev-metric",
                data={"snapshots": {}},
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={"traces": []},
            ),
        },
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_service == "payment"
    assert result.diagnosis.dependency_service is None
    assert result.diagnosis.root_cause_category == "application_failure"
    assert gateway.invoke.await_count == 1
    (first,) = gateway.invoke.await_args_list
    assert first.kwargs["output_schema"] is RcaSynthesisDraft


@pytest.mark.asyncio
async def test_multi_synthesis_binds_root_service_metric_evidence() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="storefront-api",
            root_cause_service="inventory-api",
            root_cause_summary="Inventory requests fail.",
            confidence=0.9,
            evidence_ids=["ev-trace", "ev-log"],
            customer_impact="Inventory is unavailable.",
        )
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-inventory",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True,
                tool_call_id="tc-metric",
                evidence_id="ev-metric",
                data={"snapshots": {"inventory-api": {"service_error_ratio": {"value": 0.2}}}},
            ),
            "logs": ToolEnvelope(
                ok=True,
                tool_call_id="tc-log",
                evidence_id="ev-log",
                data={"records": []},
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={"traces": []},
            ),
        },
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.evidence_ids == ["ev-trace", "ev-log", "ev-metric"]


@pytest.mark.asyncio
async def test_multi_synthesis_does_not_recast_a_root_service_as_its_own_dependency() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="checkout",
            root_cause_service="payment",
            dependency_service=None,
            root_cause_summary="Payment Charge spans fail checkout requests.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Checkout requests fail.",
        )
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-payment",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True,
                tool_call_id="tc-metric",
                evidence_id="ev-metric",
                data={"snapshots": {}},
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={"traces": []},
            ),
        },
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.dependency_service is None
    assert result.diagnosis.root_cause_category == "application_failure"


@pytest.mark.asyncio
async def test_multi_synthesis_attributes_observed_rate_limiting_to_the_caller_service() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="product-reviews",
            root_cause_service="llm",
            dependency_service="llm",
            root_cause_summary="The LLM dependency is rate limited.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Product review assistance is unavailable.",
        )
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-rate-limit",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True,
                tool_call_id="tc-metric",
                evidence_id="ev-metric",
                data={"snapshots": {}},
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={
                    "traces": [
                        {
                            "error": True,
                            "error_spans": [
                                {
                                    "service": "product-reviews",
                                    "operation": "chat",
                                    "status_code": "ERROR",
                                    "failure_type": "rate_limited",
                                }
                            ],
                        }
                    ]
                },
            ),
        },
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_service == "product-reviews"
    assert result.diagnosis.dependency_service == "llm"
    assert result.diagnosis.root_cause_category == "upstream_rate_limit"


@pytest.mark.asyncio
async def test_multi_synthesis_anchors_a_symptom_claim_to_its_unique_erroring_dependency() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="frontend",
            root_cause_service="frontend",
            dependency_service=None,
            root_cause_summary="Frontend thinks the catalog has 42 failures.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Product details fail.",
        )
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")), ModelSettings(), "fast"
    )
    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-catalog",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True, tool_call_id="tc-metric", evidence_id="ev-metric", data={"snapshots": {}}
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={
                    "traces": [
                        {
                            "error": True,
                            "error_spans": [
                                {
                                    "service": "product-catalog",
                                    "operation": "GetProduct",
                                    "status_code": "ERROR",
                                    "failure_type": None,
                                }
                            ],
                        }
                    ]
                },
            ),
        },
        strategy="json_output",
    )
    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_service == "product-catalog"
    assert result.diagnosis.dependency_service is None
    assert result.diagnosis.root_cause_category == "application_failure"
    assert (
        result.diagnosis.root_cause_summary
        == "Observed error evidence identifies product-catalog as the root-cause service."
    )


@pytest.mark.asyncio
async def test_multi_synthesis_uses_root_scoped_cache_outcomes_without_taxonomy_llm() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="feed-api",
            root_cause_service="feed-api",
            dependency_service="content-cache",
            root_cause_summary="Cache misses fail during dependency lookup.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Feed requests fail.",
        )
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
                },
                {
                    "error": True,
                    "observations": [
                        {"service": "feed-api", "attributes": {"app.cache_hit": False}}
                    ],
                },
            ]
        },
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-cache",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True,
                tool_call_id="tc-metric",
                evidence_id="ev-metric",
                data={"snapshots": {}},
            ),
            "traces": traces,
        },
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_category == "cache_failure"
    assert gateway.invoke.await_count == 1


@pytest.mark.asyncio
async def test_multi_synthesis_anchors_cache_failure_to_the_observed_cache_owner() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="recommendation",
            root_cause_service="product-catalog",
            dependency_service=None,
            root_cause_summary="Catalog appears to fail.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Recommendation responses fail.",
        )
    )
    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=build_evaluation_profile(
            LlmSettings(api_key=SecretStr("test-only")), ModelSettings(), "fast"
        ),
        incident_id="inc-cache-owner",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True, tool_call_id="tc-metric", evidence_id="ev-metric", data={"snapshots": {}}
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={
                    "traces": [
                        {
                            "error": False,
                            "observations": [
                                {"service": "recommendation", "attributes": {"app.cache_hit": True}}
                            ],
                        },
                        {
                            "error": True,
                            "error_spans": [
                                {
                                    "service": "product-catalog",
                                    "failure_type": "not_found",
                                }
                            ],
                            "observations": [
                                {
                                    "service": "recommendation",
                                    "attributes": {"app.cache_hit": False},
                                }
                            ],
                        },
                    ]
                },
            ),
        },
        strategy="json_output",
    )
    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_service == "recommendation"
    assert result.diagnosis.root_cause_category == "cache_failure"


@pytest.mark.asyncio
async def test_multi_synthesis_anchors_a_mixed_cache_path_inside_an_error_trace() -> None:
    """A cache path can emit both observations before its enclosing RPC fails."""
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        diagnosis=RcaDiagnosisDraft(
            symptom_service="feed-api",
            root_cause_service="content-api",
            dependency_service="content-api",
            root_cause_summary="Content API returns not found.",
            confidence=0.9,
            evidence_ids=["ev-metric", "ev-trace"],
            customer_impact="Feed requests fail.",
        )
    )
    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=build_evaluation_profile(
            LlmSettings(api_key=SecretStr("test-only")), ModelSettings(), "fast"
        ),
        incident_id="inc-mixed-cache-path",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True, tool_call_id="tc-metric", evidence_id="ev-metric", data={"snapshots": {}}
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={
                    "traces": [
                        {
                            "error": True,
                            "error_spans": [
                                {
                                    "service": "content-api",
                                    "failure_type": "not_found",
                                },
                                {"service": "feed-api", "failure_type": None},
                            ],
                            "observations": [
                                {"service": "feed-api", "attributes": {"app.cache_hit": False}},
                                {"service": "feed-api", "attributes": {"app.cache_hit": True}},
                            ],
                        }
                    ]
                },
            ),
        },
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_service == "feed-api"
    assert result.diagnosis.root_cause_category == "cache_failure"


@pytest.mark.asyncio
async def test_multi_synthesis_binds_missing_diagnosis_evidence_from_matching_hypothesis() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        hypotheses=[
            RootCauseHypothesis(
                id="hyp-ad",
                root_cause_service="ad",
                failure_mode="Ad requests fail.",
                confidence=0.9,
                supporting_evidence_ids=["ev-metric", "ev-trace"],
            )
        ],
        diagnosis=RcaDiagnosisDraft(
            symptom_service="frontend",
            root_cause_service="ad",
            dependency_service="ad",
            root_cause_summary="Ad requests fail.",
            confidence=0.9,
            evidence_ids=[],
            customer_impact="Homepage ads fail.",
        ),
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )
    envelopes = {
        "metrics": ToolEnvelope(
            ok=True,
            tool_call_id="tc-metric",
            evidence_id="ev-metric",
            data={"snapshots": {}},
        ),
        "traces": ToolEnvelope(
            ok=True,
            tool_call_id="tc-trace",
            evidence_id="ev-trace",
            data={"traces": []},
        ),
    }

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-ad",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes=envelopes,
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.evidence_ids == ["ev-metric", "ev-trace"]


@pytest.mark.asyncio
async def test_multi_synthesis_does_not_bind_evidence_from_an_unrelated_hypothesis() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        hypotheses=[
            RootCauseHypothesis(
                id="hyp-other",
                root_cause_service="catalog",
                failure_mode="Catalog is slow.",
                confidence=0.9,
                supporting_evidence_ids=["ev-metric", "ev-trace"],
            )
        ],
        diagnosis=RcaDiagnosisDraft(
            symptom_service="frontend",
            root_cause_service="ad",
            dependency_service="ad",
            root_cause_summary="Ad requests fail.",
            confidence=0.9,
            evidence_ids=[],
            customer_impact="Homepage ads fail.",
        ),
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-ad",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={
            "metrics": ToolEnvelope(
                ok=True,
                tool_call_id="tc-metric",
                evidence_id="ev-metric",
                data={"snapshots": {}},
            ),
            "traces": ToolEnvelope(
                ok=True,
                tool_call_id="tc-trace",
                evidence_id="ev-trace",
                data={"traces": []},
            ),
        },
        strategy="json_output",
    )

    assert result.diagnosis is None
    assert result.reason == "Diagnosis lacks two grounded realtime Evidence references."


@pytest.mark.asyncio
async def test_multi_synthesis_skips_taxonomy_when_commander_abstains() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(reason="Evidence is insufficient.")
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-1",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes={},
        strategy="json_output",
    )

    assert result.diagnosis is None
    assert result.reason == "Evidence is insufficient."
    assert gateway.invoke.await_count == 1


@pytest.mark.asyncio
async def test_multi_synthesis_terminalizes_one_eligible_model_hypothesis() -> None:
    gateway: Any = AsyncMock()
    gateway.invoke.return_value = RcaSynthesisDraft(
        hypotheses=[
            RootCauseHypothesis(
                id="hyp-catalog",
                root_cause_service="product-catalog",
                failure_mode="GetProduct fails in the catalog service.",
                confidence=0.75,
                supporting_evidence_ids=["ev-metric", "ev-trace"],
            )
        ],
        reason="Additional logs would be useful.",
    )
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")), ModelSettings(), "fast"
    )
    envelopes = {
        "metrics": ToolEnvelope(
            ok=True, tool_call_id="tc-metric", evidence_id="ev-metric", data={"snapshots": {}}
        ),
        "traces": ToolEnvelope(
            ok=True, tool_call_id="tc-trace", evidence_id="ev-trace", data={"traces": []}
        ),
    }

    result = await synthesize_with_taxonomy(
        gateway=gateway,
        profile=profile,
        incident_id="inc-catalog-terminalization",
        commander=load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"],
        commander_user_prompt='{"reports":[]}',
        taxonomy_envelopes=envelopes,
        strategy="json_output",
        symptom_service="frontend",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_service == "product-catalog"
    assert result.diagnosis.evidence_ids == ["ev-metric", "ev-trace"]
    assert result.diagnosis.root_cause_category == "application_failure"
    assert result.reason == "Deterministic terminalization of one eligible model hypothesis."


def test_cli_selects_only_the_requested_public_episode() -> None:
    episodes = load_episode_suite(
        ROOT / "scenarios",
        ROOT / "service_catalog" / "otel-demo.yaml",
    )

    selected = select_episodes(
        episodes,
        split="validation",
        scenario="payment-unreachable-001",
    )

    assert [episode.id for episode in selected] == ["payment-unreachable-001"]


def test_cli_rejects_missing_scenario_and_actions_before_m7() -> None:
    episodes = load_episode_suite(
        ROOT / "scenarios",
        ROOT / "service_catalog" / "otel-demo.yaml",
    )
    with pytest.raises(ValueError, match="unknown scenario"):
        select_episodes(episodes, split="validation", scenario="missing")

    args = build_parser().parse_args(["--mode", "baseline", "--split", "validation"])
    assert args.no_actions is False


def test_recovery_query_excludes_the_fault_window() -> None:
    episodes = load_episode_suite(
        ROOT / "scenarios",
        ROOT / "service_catalog" / "otel-demo.yaml",
    )
    episode = next(item for item in episodes if item.id == "payment-unreachable-001")
    query = build_recovery_query(
        episode.execution.recovery.checks[0],
        configured_seconds=episode.execution.recovery.observation_seconds,
    )

    assert query.window == "60s"
    assert query.duration == "60s"
    assert (query.end - query.start).total_seconds() == 60


def test_recovery_observation_waits_for_a_complete_span_metrics_window() -> None:
    assert effective_recovery_observation_seconds(30) == 60
    assert effective_recovery_observation_seconds(90) == 90


def test_recovery_observation_retries_after_collector_lag_without_relaxing_slo() -> None:
    assert recovery_observation_schedule(30) == (60, 75, 90, 105, 120)
    assert recovery_observation_schedule(90) == (90, 105, 120, 135, 150)


def test_cli_defaults_to_benchmarked_deepseek_fast_and_prices_usage() -> None:
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "fast",
    )
    record = ModelCallRecord(
        call_id="call-1",
        incident_id="incident-1",
        agent_name="baseline",
        model_profile="fast",
        prompt_version="v1",
        strategy="tool_strategy",
        attempt=1,
        status="SUCCESS",
        usage=ModelUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        latency_ms=1,
    )

    priced = price_model_call(record, profile)

    assert profile.model == "deepseek-v4-flash"
    assert priced.usage.cost_microusd == 420_000


def test_cli_prices_benchmarked_deepseek_strong_usage() -> None:
    profile = build_evaluation_profile(
        LlmSettings(api_key=SecretStr("test-only")),
        ModelSettings(),
        "strong",
    )
    record = ModelCallRecord(
        call_id="call-1",
        incident_id="incident-1",
        agent_name="baseline",
        model_profile="strong",
        prompt_version="v1",
        strategy="tool_strategy",
        attempt=1,
        status="SUCCESS",
        usage=ModelUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        latency_ms=1,
    )

    priced = price_model_call(record, profile)

    assert profile.model == "deepseek-v4-pro"
    assert priced.usage.cost_microusd == 1_305_000


def test_cli_prices_qwen36_flash_usage_with_beijing_cache_miss_rate() -> None:
    profile = build_evaluation_profile(
        LlmSettings(
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=SecretStr("test-only"),
        ),
        ModelSettings(fast="qwen3.6-flash"),
        "fast",
    )
    record = ModelCallRecord(
        call_id="call-1",
        incident_id="incident-1",
        agent_name="baseline",
        model_profile="fast",
        prompt_version="v1",
        strategy="json_output",
        attempt=1,
        status="SUCCESS",
        usage=ModelUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        latency_ms=1,
    )

    priced = price_model_call(record, profile)

    assert priced.usage.cost_microusd == 1_155_000


def test_cli_prices_qwen37_flash_usage_with_first_tier_rate() -> None:
    profile = build_evaluation_profile(
        LlmSettings(
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=SecretStr("test-only"),
        ),
        ModelSettings(fast="qwen3.7-flash"),
        "fast",
    )
    record = ModelCallRecord(
        call_id="call-1",
        incident_id="incident-1",
        agent_name="baseline",
        model_profile="fast",
        prompt_version="v1",
        strategy="json_output",
        attempt=1,
        status="SUCCESS",
        usage=ModelUsage(input_tokens=10_000, output_tokens=10_000),
        latency_ms=1,
    )

    priced = price_model_call(record, profile)

    assert priced.usage.cost_microusd == 1_600


def test_cli_prices_qwen37_plus_usage_with_beijing_list_rate() -> None:
    profile = build_evaluation_profile(
        LlmSettings(
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=SecretStr("test-only"),
        ),
        ModelSettings(fast="qwen3.7-plus"),
        "fast",
    )
    record = ModelCallRecord(
        call_id="call-1",
        incident_id="incident-1",
        agent_name="baseline",
        model_profile="fast",
        prompt_version="v1",
        strategy="json_output",
        attempt=1,
        status="SUCCESS",
        usage=ModelUsage(input_tokens=10_000, output_tokens=10_000),
        latency_ms=1,
    )

    priced = price_model_call(record, profile)

    assert priced.usage.cost_microusd == 13_770


def test_checkout_traffic_driver_requires_each_request_to_hit_the_fault() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(500 if request.url.path == "/api/checkout" else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        drive_otel_demo_traffic(
            client,
            TrafficSpec(adapter="otel-demo-http", operation="checkout", requests=2),
            base_url="http://demo",
            settle_seconds=0,
        )

    assert seen == [
        "/api/products/0PUK6V6EV0",
        "/api/cart",
        "/api/checkout",
        "/api/products/0PUK6V6EV0",
        "/api/cart",
        "/api/checkout",
    ]

    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
        ) as client,
        pytest.raises(RuntimeError, match="did not exercise the configured fault"),
    ):
        drive_otel_demo_traffic(
            client,
            TrafficSpec(adapter="otel-demo-http", operation="checkout", requests=1),
            base_url="http://demo",
            settle_seconds=0,
        )


@pytest.mark.parametrize(
    ("operation", "fault_path", "fault_status"),
    [
        ("ads", "/api/data", 500),
        ("product_detail", "/api/products/OLJCESPC7Z", 500),
        ("ai_assistant", "/api/product-ask-ai-assistant/6E92ZMYYFZ", 500),
        ("cart", "/api/cart", 500),
        ("recommendations", "/api/recommendations", 500),
    ],
)
def test_validation_traffic_drivers_exercise_the_selected_path(
    operation: str,
    fault_path: str,
    fault_status: int,
) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(fault_status, json={})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        drive_otel_demo_traffic(
            client,
            TrafficSpec.model_validate(
                {"adapter": "otel-demo-http", "operation": operation, "requests": 2}
            ),
            base_url="http://demo",
            settle_seconds=0,
        )

    assert [request.url.path for request in seen] == [fault_path] * (
        1 if operation in {"ads", "ai_assistant"} else 2
    )
    if operation == "cart":
        assert all(request.method == "DELETE" for request in seen)
    if operation == "ads":
        assert all(request.url.params["contextKeys"] == "ad" for request in seen)
        statuses: Iterator[httpx.Response] = iter(
            (httpx.Response(200, json={}), httpx.Response(500, json={}))
        )
        with httpx.Client(transport=httpx.MockTransport(lambda _: next(statuses))) as client:
            drive_otel_demo_traffic(
                client,
                TrafficSpec(adapter="otel-demo-http", operation="ads", requests=2),
                base_url="http://demo",
                settle_seconds=0,
            )

    if operation == "ai_assistant":
        assert all(request.method == "POST" for request in seen)
        assistant_statuses: Iterator[httpx.Response] = iter(
            (
                httpx.Response(200, json="healthy response"),
                httpx.Response(
                    200,
                    json=("The system is unable to process your response. Please try again later."),
                ),
            )
        )
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: next(assistant_statuses))
        ) as client:
            drive_otel_demo_traffic(
                client,
                TrafficSpec(adapter="otel-demo-http", operation="ai_assistant", requests=2),
                base_url="http://demo",
                settle_seconds=0,
            )

        with (
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
            ) as client,
            pytest.raises(RuntimeError, match="did not exercise the configured fault"),
        ):
            drive_otel_demo_traffic(
                client,
                TrafficSpec(adapter="otel-demo-http", operation="ai_assistant", requests=2),
                base_url="http://demo",
                settle_seconds=0,
            )

        with (
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
            ) as client,
            pytest.raises(RuntimeError, match="did not exercise the configured fault"),
        ):
            drive_otel_demo_traffic(
                client,
                TrafficSpec(adapter="otel-demo-http", operation="ads", requests=2),
                base_url="http://demo",
                settle_seconds=0,
            )

    if operation == "recommendations":
        response_codes: Iterator[int] = iter((200, 500))
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(next(response_codes), json={}))
        ) as client:
            drive_otel_demo_traffic(
                client,
                TrafficSpec(adapter="otel-demo-http", operation="recommendations", requests=2),
                base_url="http://demo",
                settle_seconds=0,
            )

        with (
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
            ) as client,
            pytest.raises(RuntimeError, match="did not exercise the configured fault"),
        ):
            drive_otel_demo_traffic(
                client,
                TrafficSpec(adapter="otel-demo-http", operation="recommendations", requests=2),
                base_url="http://demo",
                settle_seconds=0,
            )


def test_correlated_log_evidence_is_attached_to_an_existing_trace_diagnosis() -> None:
    envelopes = {
        "traces": ToolEnvelope(
            ok=True,
            tool_call_id="tc-trace",
            evidence_id="ev-trace",
            data={"traces": [{"trace_id": "a" * 32}]},
        ),
        "logs": ToolEnvelope(
            ok=True,
            tool_call_id="tc-log",
            evidence_id="ev-log",
            data={"records": [{"trace_id": "a" * 32, "body": "[PlaceOrder]"}]},
        ),
    }

    assert bind_correlated_log_evidence(["ev-metric", "ev-trace"], envelopes) == [
        "ev-metric",
        "ev-trace",
        "ev-log",
    ]
