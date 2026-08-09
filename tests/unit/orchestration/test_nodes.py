from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.diagnosis import (
    Diagnosis,
    InvestigationFinding,
    InvestigationReport,
    RootCauseHypothesis,
)
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.orchestration.nodes.investigate_metrics import InvestigationNode
from incidentpilot.orchestration.nodes.prepare_context import PrepareContextNode
from incidentpilot.orchestration.nodes.report import ReportNode
from incidentpilot.orchestration.nodes.synthesize import SynthesizeNode
from incidentpilot.orchestration.nodes.triage import TriageNode
from incidentpilot.orchestration.state import (
    IncidentIdentity,
    InvestigationBudget,
    InvestigationTask,
    PreparedContext,
    ReportArtifact,
    ServiceContext,
    SynthesisDraft,
    TriageDecision,
    WaveReport,
)

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
RANGE = TimeRange(start=NOW, end=NOW)


def _evidence(evidence_id: str, kind: EvidenceKind, *, incident_id: str = "inc-1") -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        incident_id=incident_id,
        kind=kind,
        source_system=kind.value,
        query={},
        observed_range=RANGE,
        summary=f"{kind.value} signal",
        raw_digest_sha256="a" * 64,
        collected_at=NOW,
    )


class FakeContextLoader:
    def __init__(self, *, tenant_id: str = "tenant-1") -> None:
        self.tenant_id = tenant_id
        self.calls: list[str] = []

    async def get_incident_identity(self, incident_id: str) -> IncidentIdentity | None:
        self.calls.append("identity")
        return IncidentIdentity(incident_id=incident_id, tenant_id=self.tenant_id)

    async def load_service_catalog(self) -> list[ServiceContext]:
        self.calls.append("catalog")
        return [
            ServiceContext(name="checkout", dependencies=["payment"], owner="checkout-team"),
            ServiceContext(name="payment", dependencies=[], owner="payment-team"),
        ]

    async def load_recent_changes(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        services: list[str],
        time_range: TimeRange,
    ) -> list[EvidenceRef]:
        self.calls.append("changes")
        return [_evidence("ev-change", EvidenceKind.CHANGE, incident_id=incident_id)]


async def test_prepare_context_uses_server_loader_and_checks_tenant_binding() -> None:
    loader = FakeContextLoader()
    result = await PrepareContextNode(loader)(
        {
            "incident_id": "inc-1",
            "tenant_id": "tenant-1",
            "scoped_services": ["checkout"],
            "time_range": RANGE.model_dump(mode="json"),
        }
    )

    assert loader.calls == ["identity", "catalog", "changes"]
    prepared = PreparedContext.model_validate(result["prepared_context"])
    assert prepared.recent_change_evidence_ids == ("ev-change",)
    assert result["evidence_ids"] == ["ev-change"]

    with pytest.raises(DomainInvariantError, match="tenant"):
        await PrepareContextNode(FakeContextLoader(tenant_id="tenant-2"))(
            {
                "incident_id": "inc-1",
                "tenant_id": "tenant-1",
                "scoped_services": ["checkout"],
                "time_range": RANGE.model_dump(mode="json"),
            }
        )


class FakeTriageAgent:
    def __init__(self) -> None:
        self.seen: PreparedContext | None = None

    async def triage(self, context: PreparedContext) -> TriageDecision:
        self.seen = context
        return TriageDecision(
            scoped_services=["checkout", "payment"],
            investigators=["metrics", "traces"],
            objectives={"metrics": "Measure errors", "traces": "Locate dependency failure"},
        )


async def test_triage_only_receives_prepared_context() -> None:
    context = PreparedContext(
        incident_id="inc-1",
        tenant_id="tenant-1",
        services=(
            ServiceContext(name="checkout", dependencies=["payment"], owner="checkout-team"),
            ServiceContext(name="payment", dependencies=[], owner="payment-team"),
        ),
        recent_change_evidence_ids=(),
    )
    agent = FakeTriageAgent()

    result = await TriageNode(agent)({"prepared_context": context.model_dump(mode="json")})

    assert agent.seen == context
    assert TriageDecision.model_validate(result["triage"]).investigators == [
        "metrics",
        "traces",
    ]
    assert result["status"] == IncidentStatus.INVESTIGATING.value


class FakeInvestigator:
    async def investigate(self, task: InvestigationTask) -> InvestigationReport:
        return InvestigationReport(
            investigator="metrics",
            scope_services=task.scope_services,
            findings=[
                InvestigationFinding(
                    statement="Error ratio increased",
                    evidence_ids=["ev-metric"],
                    signal_strength=0.9,
                )
            ],
            tool_call_ids=["tc-1"],
        )


class FakeReferenceValidator:
    def __init__(self, evidence: list[EvidenceRef]) -> None:
        self.evidence = evidence

    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        return next((item for item in self.evidence if item.id == evidence_id), None)

    async def tool_call_belongs_to_incident(self, tool_call_id: str, incident_id: str) -> bool:
        return tool_call_id == "tc-1" and incident_id == "inc-1"


async def test_investigation_node_only_merges_persisted_references() -> None:
    task = InvestigationTask(
        wave=1,
        investigator="metrics",
        scope_services=["checkout"],
        objective="Measure errors",
    )
    result = await InvestigationNode(
        investigator="metrics",
        agent=FakeInvestigator(),
        references=FakeReferenceValidator([_evidence("ev-metric", EvidenceKind.METRIC)]),
    )({"incident_id": "inc-1", "task": task.model_dump(mode="json")})

    assert result["evidence_ids"] == ["ev-metric"]
    assert result["tool_call_ids"] == ["tc-1"]
    assert WaveReport.model_validate(result["reports"][0]).wave == 1

    with pytest.raises(DomainInvariantError, match="does not exist"):
        await InvestigationNode(
            investigator="metrics",
            agent=FakeInvestigator(),
            references=FakeReferenceValidator([]),
        )({"incident_id": "inc-1", "task": task})


def _hypothesis(confidence: float = 0.6) -> RootCauseHypothesis:
    return RootCauseHypothesis(
        id="hyp-1",
        root_cause_service="payment",
        failure_mode="Payment calls fail",
        confidence=confidence,
        supporting_evidence_ids=["ev-metric"],
    )


class FakeSynthesisAgent:
    def __init__(self, draft: SynthesisDraft) -> None:
        self.draft = draft

    async def synthesize(self, state: dict[str, Any]) -> SynthesisDraft:
        return self.draft


async def test_synthesize_requests_next_wave_then_stops_at_budget() -> None:
    task = InvestigationTask(
        wave=2,
        investigator="logs",
        scope_services=["payment"],
        objective="Find payment errors",
    )
    draft = SynthesisDraft(hypotheses=[_hypothesis()], next_wave_tasks=[task])
    node = SynthesizeNode(
        FakeSynthesisAgent(draft),
        FakeReferenceValidator([_evidence("ev-metric", EvidenceKind.METRIC)]),
    )

    continuing = await node(
        {
            "incident_id": "inc-1",
            "investigation_budget": InvestigationBudget(
                wave=1, max_waves=2, read_calls_used=4, max_read_calls=10
            ).model_dump(mode="json"),
        }
    )
    assert continuing["status"] == IncidentStatus.INVESTIGATING.value
    assert InvestigationBudget.model_validate(continuing["investigation_budget"]).wave == 2
    assert [InvestigationTask.model_validate(item) for item in continuing["next_wave_tasks"]] == [
        task
    ]

    exhausted = await node(
        {
            "incident_id": "inc-1",
            "investigation_budget": InvestigationBudget(
                wave=2, max_waves=2, read_calls_used=10, max_read_calls=10
            ).model_dump(mode="json"),
        }
    )
    assert exhausted["status"] == IncidentStatus.NEEDS_HUMAN.value
    assert exhausted["diagnosis"] is None
    assert exhausted["next_wave_tasks"] == []


async def test_synthesize_confirms_only_grounded_high_confidence_diagnosis() -> None:
    diagnosis = Diagnosis(
        symptom_service="checkout",
        root_cause_service="payment",
        root_cause_category="dependency_failure",
        root_cause_summary="Payment calls fail",
        confidence=0.9,
        evidence_ids=["ev-metric", "ev-trace"],
        customer_impact="Orders fail",
    )
    node = SynthesizeNode(
        FakeSynthesisAgent(SynthesisDraft(hypotheses=[_hypothesis(0.9)], diagnosis=diagnosis)),
        FakeReferenceValidator(
            [
                _evidence("ev-metric", EvidenceKind.METRIC),
                _evidence("ev-trace", EvidenceKind.TRACE),
            ]
        ),
    )

    result = await node(
        {
            "incident_id": "inc-1",
            "investigation_budget": InvestigationBudget(
                wave=1, max_waves=2, read_calls_used=2, max_read_calls=10
            ).model_dump(mode="json"),
        }
    )
    assert result["status"] == IncidentStatus.DIAGNOSED.value
    assert Diagnosis.model_validate(result["diagnosis"]) == diagnosis


async def test_report_is_deterministic_and_contains_only_state_facts() -> None:
    state: dict[str, Any] = {
        "incident_id": "inc-1",
        "status": IncidentStatus.NEEDS_HUMAN,
        "evidence_ids": ["ev-metric"],
        "hypotheses": [_hypothesis()],
        "terminal_reason": "Investigation budget exhausted",
    }

    first = await ReportNode()(state)
    second = await ReportNode()(state)

    assert first == second
    artifact = ReportArtifact.model_validate(first["report"])
    assert artifact.json_data["incident_id"] == "inc-1"
    assert "ev-metric" in artifact.markdown
    assert "Invented remediation" not in artifact.markdown
