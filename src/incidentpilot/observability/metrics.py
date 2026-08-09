from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider


class OperationalMetrics:
    """Bounded operational metrics; labels intentionally exclude incident and tenant IDs."""

    def __init__(self, provider: MeterProvider) -> None:
        meter = provider.get_meter("incidentpilot")
        self._agent_calls = meter.create_counter("incidentpilot.agent.calls")
        self._agent_duration = meter.create_histogram("incidentpilot.agent.duration", unit="ms")
        self._tool_calls = meter.create_counter("incidentpilot.tool.calls")
        self._tool_duration = meter.create_histogram("incidentpilot.tool.duration", unit="ms")
        self._model_tokens = meter.create_counter("incidentpilot.model.tokens")
        self._model_cost = meter.create_counter("incidentpilot.model.cost", unit="usd")
        self._action_calls = meter.create_counter("incidentpilot.action.calls")
        self._recovery_outcomes = meter.create_counter("incidentpilot.recovery.outcomes")
        self._approval_wait = meter.create_histogram("incidentpilot.approval.wait", unit="ms")

    def record_agent(self, name: str, duration_ms: int, *, success: bool) -> None:
        attributes = {"agent.name": name, "success": success}
        self._agent_calls.add(1, attributes)
        self._agent_duration.record(duration_ms, attributes)

    def record_tool(self, name: str, duration_ms: int, *, success: bool) -> None:
        attributes = {"tool.name": name, "success": success}
        self._tool_calls.add(1, attributes)
        self._tool_duration.record(duration_ms, attributes)

    def record_model(
        self,
        *,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microusd: int,
    ) -> None:
        attributes = {"agent.name": agent_name, "gen_ai.request.model": model}
        self._model_tokens.add(input_tokens, {**attributes, "token.type": "input"})
        self._model_tokens.add(output_tokens, {**attributes, "token.type": "output"})
        self._model_cost.add(cost_microusd / 1_000_000, attributes)

    def record_action(self, name: str, *, success: bool) -> None:
        self._action_calls.add(1, {"action.name": name, "success": success})

    def record_recovery(self, *, recovered: bool) -> None:
        self._recovery_outcomes.add(1, {"recovered": recovered})

    def record_approval_wait(self, duration_ms: int, *, decision: str) -> None:
        self._approval_wait.record(duration_ms, {"decision": decision})
