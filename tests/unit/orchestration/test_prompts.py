from __future__ import annotations

from pathlib import Path

import pytest

from incidentpilot.orchestration.prompts import AgentFactory, load_prompt_set

ROOT = Path(__file__).parents[3]
AGENTS = {
    "triage",
    "metrics_investigator",
    "logs_investigator",
    "traces_investigator",
    "runbook_analyst",
    "incident_commander",
    "remediation_planner",
    "postmortem_reporter",
}
AVAILABLE_TOOLS = {
    name: object()
    for name in (
        "query_metrics",
        "list_metric_names",
        "get_service_health_snapshot",
        "search_logs",
        "get_log_context",
        "aggregate_log_patterns",
        "search_traces",
        "get_trace",
        "get_service_dependencies",
        "search_runbooks",
        "get_runbook_section",
        "search_similar_incidents",
        "list_allowed_actions",
        "restart_service",
        "rollback_change",
    )
}


def test_prompt_loader_validates_all_agents_and_stable_digests() -> None:
    prompts = load_prompt_set(ROOT / "prompts" / "v1")

    assert prompts.version == "v1"
    assert set(prompts.prompts) == AGENTS
    assert all(len(prompt.digest) == 64 for prompt in prompts.prompts.values())
    assert load_prompt_set(ROOT / "prompts" / "v1") == prompts

    combined = "\n".join(prompt.content.lower() for prompt in prompts.prompts.values())
    for forbidden in ("paymentfailure", "scenario_key", "ground_truth", "holdout"):
        assert forbidden not in combined


def test_agent_factory_exposes_only_each_prompt_tool_allowlist() -> None:
    prompts = load_prompt_set(ROOT / "prompts" / "v1")
    factory = AgentFactory(prompts=prompts, available_tools=AVAILABLE_TOOLS)

    logs = factory.build("logs_investigator")
    assert logs.tool_names == (
        "search_logs",
        "get_log_context",
        "aggregate_log_patterns",
    )
    assert "query_metrics" not in logs.tool_names
    assert logs.tools == tuple(AVAILABLE_TOOLS[name] for name in logs.tool_names)
    assert (
        factory.build("triage")
        .invocation(
            incident_id="inc-1",
            user_prompt="classify this alert",
        )
        .prompt_version
        == "v1"
    )

    for name in (
        "metrics_investigator",
        "logs_investigator",
        "traces_investigator",
        "runbook_analyst",
    ):
        tools = factory.build(name).tool_names
        assert "restart_service" not in tools
        assert "rollback_change" not in tools

    with pytest.raises(ValueError, match="missing required tools"):
        AgentFactory(prompts=prompts, available_tools={}).build("logs_investigator")


def test_commander_terminal_rules_distinguish_abstention_from_diagnosis() -> None:
    content = load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"].content
    commander = " ".join(content.lower().split())

    assert "false alert" in commander
    assert "diagnosis=null" in commander
    assert "confidence >= 0.75" in commander
    assert "two existing evidence ids from distinct real-time signal kinds" in commander
    assert "must return a terminal diagnosis" in commander
    assert "only when no hypothesis meets all terminal invariants" in commander
    assert "missing a third signal kind" in commander
    assert "diagnosis_limits" in commander
    assert "fewer than three hypotheses is valid" in commander
    assert "never emit an unsupported placeholder hypothesis" in commander
    assert "outgoing rpc client span" in commander
    assert "no matching target server span" in commander
    assert "root_cause_service to the caller" in commander
    assert "do not synthesize range endpoints" in commander
    assert "not_found" in commander
    assert "caller cache path" in commander
    assert "container_memory_usage" in commander
    assert "storage_connection_failure" in commander
    assert "same service, operation or request path" in commander
    assert "uncorrelated successful requests" in commander
    for category in (
        "application_failure",
        "dependency_failure",
        "dependency_unreachable",
        "upstream_rate_limit",
        "cache_failure",
    ):
        assert category in commander
    assert "symptom service differs from the root-cause service" in commander
