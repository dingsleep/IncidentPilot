from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.domain.diagnosis import Diagnosis, validate_diagnosis
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.usage import ModelUsage
from incidentpilot.orchestration.state import InvestigationBudget

_FORBIDDEN_TOOLS = frozenset(
    {
        "restart_service",
        "rollback_change",
        "scale_deployment",
        "shell",
        "sql",
        "kubectl_exec",
        "request_url",
    }
)


class BaselineRequest(DomainModel):
    incident_id: str = Field(min_length=1, max_length=64)
    context: dict[str, Any]
    investigation_budget: InvestigationBudget


class BaselineAgentOutput(DomainModel):
    diagnosis: Diagnosis
    tool_call_ids: list[str] = Field(max_length=100)
    usage: ModelUsage


class BaselineMetrics(DomainModel):
    tool_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class BaselineRun(DomainModel):
    incident_id: str
    model_profile: str
    diagnosis: Diagnosis
    metrics: BaselineMetrics


class BaselineAccuracy(DomainModel):
    correct: int = Field(ge=0)
    total: int = Field(ge=1)
    accuracy: float = Field(ge=0, le=1)


class BaselineAgent(Protocol):
    async def diagnose(
        self,
        *,
        request: BaselineRequest,
        profile: ModelProfile,
        tools: tuple[object, ...],
    ) -> BaselineAgentOutput: ...


class BaselineReferenceStore(Protocol):
    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None: ...

    async def tool_call_belongs_to_incident(
        self,
        tool_call_id: str,
        incident_id: str,
    ) -> bool: ...


class BaselineRunner:
    """Run exactly one unrestricted-read diagnosis agent for fair comparison."""

    def __init__(
        self,
        *,
        agent: BaselineAgent,
        profile: ModelProfile,
        tools: dict[str, object],
        references: BaselineReferenceStore,
    ) -> None:
        forbidden = set(tools).intersection(_FORBIDDEN_TOOLS)
        if forbidden:
            raise ValueError(f"baseline tools must be read-only: {sorted(forbidden)}")
        self._agent = agent
        self._profile = profile
        self._tools = tuple(tools.values())
        self._references = references

    async def run(self, request: BaselineRequest) -> BaselineRun:
        started = perf_counter()
        output = await self._agent.diagnose(
            request=request,
            profile=self._profile,
            tools=self._tools,
        )
        tool_call_ids = list(dict.fromkeys(output.tool_call_ids))
        remaining_calls = (
            request.investigation_budget.max_read_calls
            - request.investigation_budget.read_calls_used
        )
        if len(tool_call_ids) != len(output.tool_call_ids):
            raise DomainInvariantError("baseline returned duplicate tool call IDs")
        if len(tool_call_ids) > remaining_calls:
            raise DomainInvariantError("baseline exceeded the shared read-call budget")
        for tool_call_id in tool_call_ids:
            if not await self._references.tool_call_belongs_to_incident(
                tool_call_id,
                request.incident_id,
            ):
                raise DomainInvariantError(f"tool call does not belong to incident: {tool_call_id}")

        evidence: list[EvidenceRef] = []
        for evidence_id in output.diagnosis.evidence_ids:
            item = await self._references.get_evidence(evidence_id)
            if item is None:
                raise DomainInvariantError(f"evidence does not exist: {evidence_id}")
            evidence.append(item)
        validate_diagnosis(
            output.diagnosis,
            evidence,
            incident_id=request.incident_id,
        )
        return BaselineRun(
            incident_id=request.incident_id,
            model_profile=self._profile.name,
            diagnosis=output.diagnosis,
            metrics=BaselineMetrics(
                tool_call_count=len(tool_call_ids),
                input_tokens=output.usage.input_tokens,
                output_tokens=output.usage.output_tokens,
                cost_microusd=output.usage.cost_microusd,
                duration_ms=int((perf_counter() - started) * 1000),
            ),
        )


def evaluate_root_cause_accuracy(
    *,
    predictions: dict[str, str],
    expected: dict[str, str],
) -> BaselineAccuracy:
    if not expected:
        raise ValueError("expected root causes must not be empty")
    if set(predictions) != set(expected):
        raise ValueError("predictions and expected incidents must match")
    correct = sum(
        predictions[incident_id] == root_cause for incident_id, root_cause in expected.items()
    )
    return BaselineAccuracy(
        correct=correct,
        total=len(expected),
        accuracy=correct / len(expected),
    )
