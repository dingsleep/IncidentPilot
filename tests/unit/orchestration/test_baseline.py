from __future__ import annotations

from datetime import UTC, datetime

import pytest

from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.diagnosis import Diagnosis
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.usage import ModelUsage
from incidentpilot.orchestration.baseline import (
    BaselineAgentOutput,
    BaselineRequest,
    BaselineRunner,
    evaluate_root_cause_accuracy,
)
from incidentpilot.orchestration.state import InvestigationBudget

NOW = datetime(2026, 7, 17, 14, 0, tzinfo=UTC)


def _profile() -> ModelProfile:
    return ModelProfile(
        name="strong",
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        temperature=0,
        max_tokens=8000,
        supports_tools=True,
        supports_native_schema=False,
    )


def _evidence(evidence_id: str, kind: EvidenceKind) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        incident_id="inc-1",
        kind=kind,
        source_system=kind.value,
        query={},
        observed_range=TimeRange(start=NOW, end=NOW),
        summary=f"{kind.value} evidence",
        source_uri=f"{kind.value}://source",
        raw_digest_sha256="a" * 64,
        collected_at=NOW,
    )


class FakeReferences:
    def __init__(self) -> None:
        self.evidence = {
            "ev-metric": _evidence("ev-metric", EvidenceKind.METRIC),
            "ev-trace": _evidence("ev-trace", EvidenceKind.TRACE),
        }

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        return self.evidence.get(evidence_id)

    async def tool_call_belongs_to_incident(self, tool_call_id: str, incident_id: str) -> bool:
        return incident_id == "inc-1" and tool_call_id in {"tc-1", "tc-2"}


class CapturingAgent:
    calls = 0
    seen_profile: ModelProfile | None = None
    seen_tools: tuple[object, ...] = ()
    seen_request: BaselineRequest | None = None

    async def diagnose(
        self,
        *,
        request: BaselineRequest,
        profile: ModelProfile,
        tools: tuple[object, ...],
    ) -> BaselineAgentOutput:
        self.calls += 1
        self.seen_profile = profile
        self.seen_tools = tools
        self.seen_request = request
        return BaselineAgentOutput(
            diagnosis=Diagnosis(
                symptom_service="checkout",
                root_cause_service="payment",
                root_cause_category="dependency_failure",
                root_cause_summary="Payment calls fail",
                confidence=0.9,
                evidence_ids=["ev-metric", "ev-trace"],
                customer_impact="Orders fail",
            ),
            tool_call_ids=["tc-1", "tc-2"],
            usage=ModelUsage(input_tokens=100, output_tokens=40),
        )


async def test_baseline_uses_one_agent_with_same_profile_tools_budget_and_schema() -> None:
    profile = _profile()
    metric_tool = object()
    trace_tool = object()
    agent = CapturingAgent()
    budget = InvestigationBudget(
        wave=1,
        max_waves=2,
        read_calls_used=0,
        max_read_calls=8,
    )
    request = BaselineRequest(
        incident_id="inc-1",
        context={"alert": {"service": "checkout"}, "evidence_ids": []},
        investigation_budget=budget,
    )
    runner = BaselineRunner(
        agent=agent,
        profile=profile,
        tools={"query_metrics": metric_tool, "search_traces": trace_tool},
        references=FakeReferences(),
    )

    result = await runner.run(request)

    assert agent.calls == 1
    assert agent.seen_profile == profile
    assert agent.seen_tools == (metric_tool, trace_tool)
    assert agent.seen_request == request
    assert "expected_root_cause" not in request.model_dump(mode="json")
    assert isinstance(result.diagnosis, Diagnosis)
    assert result.model_profile == "strong"
    assert result.metrics.tool_call_count == 2
    assert result.metrics.input_tokens == 100
    assert result.metrics.output_tokens == 40
    assert result.metrics.duration_ms >= 0


def test_baseline_rejects_write_tools_and_scores_accuracy_outside_agent_input() -> None:
    with pytest.raises(ValueError, match="read-only"):
        BaselineRunner(
            agent=CapturingAgent(),
            profile=_profile(),
            tools={"query_metrics": object(), "restart_service": object()},
            references=FakeReferences(),
        )

    score = evaluate_root_cause_accuracy(
        predictions={"inc-1": "payment", "inc-2": "checkout"},
        expected={"inc-1": "payment", "inc-2": "shipping"},
    )
    assert score.correct == 1
    assert score.total == 2
    assert score.accuracy == 0.5
