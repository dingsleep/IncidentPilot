from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.diagnosis import InvestigationReport
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.orchestration.state import (
    InvestigationTask,
    Investigator,
    WaveReport,
)

_ALLOWED_EVIDENCE: dict[Investigator, frozenset[EvidenceKind]] = {
    "metrics": frozenset({EvidenceKind.METRIC}),
    "logs": frozenset({EvidenceKind.LOG}),
    "traces": frozenset({EvidenceKind.TRACE, EvidenceKind.TOPOLOGY}),
    "runbook": frozenset({EvidenceKind.RUNBOOK}),
}


class InvestigatorAgent(Protocol):
    async def investigate(self, task: InvestigationTask) -> InvestigationReport: ...


class InvestigationReferenceStore(Protocol):
    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None: ...

    async def tool_call_belongs_to_incident(
        self,
        tool_call_id: str,
        incident_id: str,
    ) -> bool: ...


class InvestigationNode:
    def __init__(
        self,
        *,
        investigator: Investigator,
        agent: InvestigatorAgent,
        references: InvestigationReferenceStore,
    ) -> None:
        self._investigator: Investigator = investigator
        self._agent = agent
        self._references = references

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(state["incident_id"])
        task = InvestigationTask.model_validate(state["task"])
        if task.investigator != self._investigator:
            raise DomainInvariantError("investigation task was sent to the wrong node")
        report = await self._agent.investigate(task)
        if report.investigator != self._investigator:
            raise DomainInvariantError("investigator returned the wrong report type")
        if not set(report.scope_services).issubset(task.scope_services):
            raise DomainInvariantError("investigator expanded its assigned service scope")

        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for finding in [*report.findings, *report.contradictions]
                for evidence_id in finding.evidence_ids
            )
        )
        for evidence_id in evidence_ids:
            evidence = await self._references.get_evidence(evidence_id)
            if evidence is None:
                raise DomainInvariantError(f"evidence does not exist: {evidence_id}")
            if evidence.incident_id != incident_id:
                raise DomainInvariantError("evidence belongs to another incident")
            if evidence.kind not in _ALLOWED_EVIDENCE[self._investigator]:
                raise DomainInvariantError("evidence kind is not allowed for investigator")

        tool_call_ids = list(dict.fromkeys(report.tool_call_ids))
        for tool_call_id in tool_call_ids:
            if not await self._references.tool_call_belongs_to_incident(
                tool_call_id,
                incident_id,
            ):
                raise DomainInvariantError(f"tool call does not belong to incident: {tool_call_id}")
        return {
            "reports": [WaveReport(wave=task.wave, report=report).model_dump(mode="json")],
            "evidence_ids": evidence_ids,
            "tool_call_ids": tool_call_ids,
        }
