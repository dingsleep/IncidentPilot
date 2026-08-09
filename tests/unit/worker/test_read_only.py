import pytest

from incidentpilot.worker.read_only import runtime_input_from_state


def test_runtime_input_uses_server_validated_alert_and_budget() -> None:
    runtime_input = runtime_input_from_state(
        {
            "alert": {
                "title": "Checkout failures",
                "severity": "P1",
                "service_hint": "checkout",
                "external_id": "alert-1",
                "source": "manual",
                "starts_at": "2026-08-01T00:00:00Z",
            },
            "investigation_budget": {"max_read_calls": 9},
        }
    )

    assert runtime_input.alert.service_hint == "checkout"
    assert runtime_input.budgets.max_read_tool_calls == 9


def test_runtime_input_rejects_state_without_a_budget() -> None:
    with pytest.raises(ValueError, match="investigation budget"):
        runtime_input_from_state(
            {"alert": {"title": "Checkout failures", "severity": "P1"}}
        )
