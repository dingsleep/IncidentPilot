from __future__ import annotations

from typing import Any, cast

from incidentpilot.evaluation.loader import EpisodeAlert, EpisodeBudgets, RuntimeEpisodeInput


def runtime_input_from_state(state: dict[str, Any]) -> RuntimeEpisodeInput:
    """Convert server-loaded, incident-scoped state into bounded agent input."""
    raw_alert_value = state.get("alert")
    raw_budget_value = state.get("investigation_budget")
    if not isinstance(raw_alert_value, dict):
        raise ValueError("server state is missing an alert")
    if not isinstance(raw_budget_value, dict):
        raise ValueError("server state is missing an investigation budget")
    raw_budget = cast(dict[str, Any], raw_budget_value)
    max_read_calls = raw_budget.get("max_read_calls")
    if not isinstance(max_read_calls, int):
        raise ValueError("server state is missing an investigation budget")
    raw_alert = cast(dict[str, Any], raw_alert_value)
    alert = EpisodeAlert.model_validate(
        {name: raw_alert[name] for name in EpisodeAlert.model_fields if name in raw_alert}
    )
    return RuntimeEpisodeInput(
        alert=alert,
        budgets=EpisodeBudgets(
            max_duration_seconds=600,
            max_read_tool_calls=max_read_calls,
            max_model_tokens=32_000,
        ),
    )
