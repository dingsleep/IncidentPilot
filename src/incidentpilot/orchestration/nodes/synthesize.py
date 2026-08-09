from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.diagnosis import eligible_for_auto_planning, validate_diagnosis
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.orchestration.state import (
    InvestigationBudget,
    SynthesisDraft,
)


class SynthesisAgent(Protocol):
    async def synthesize(self, state: dict[str, Any]) -> SynthesisDraft: ...


class EvidenceReferenceStore(Protocol):
    async def get_evidence(self, evidence_id: str) -> EvidenceRef | None: ...


class SynthesizeNode:
    def __init__(self, agent: SynthesisAgent, references: EvidenceReferenceStore) -> None:
        self._agent = agent
        self._references = references

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        draft = await self._agent.synthesize(dict(state))
        incident_id = state["incident_id"]
        budget = InvestigationBudget.model_validate(state["investigation_budget"])
        await self._validate_hypotheses(draft, incident_id)

        if draft.diagnosis is not None and eligible_for_auto_planning(draft.diagnosis):
            evidence = await self._load_evidence(draft.diagnosis.evidence_ids, incident_id)
            validate_diagnosis(draft.diagnosis, evidence, incident_id=incident_id)
            return {
                "hypotheses": [item.model_dump(mode="json") for item in draft.hypotheses],
                "diagnosis": draft.diagnosis.model_dump(mode="json"),
                "next_wave_tasks": [],
                "status": IncidentStatus.DIAGNOSED.value,
            }

        if budget.can_continue:
            if not draft.next_wave_tasks:
                raise DomainInvariantError(
                    "low-confidence synthesis requires targeted next-wave tasks"
                )
            next_wave = budget.wave + 1
            if any(task.wave != next_wave for task in draft.next_wave_tasks):
                raise DomainInvariantError("next-wave task has an invalid wave number")
            return {
                "hypotheses": [item.model_dump(mode="json") for item in draft.hypotheses],
                "diagnosis": None,
                "next_wave_tasks": [item.model_dump(mode="json") for item in draft.next_wave_tasks],
                "investigation_budget": budget.model_copy(update={"wave": next_wave}).model_dump(
                    mode="json"
                ),
                "status": IncidentStatus.INVESTIGATING.value,
            }

        return {
            "hypotheses": [item.model_dump(mode="json") for item in draft.hypotheses],
            "diagnosis": None,
            "next_wave_tasks": [],
            "status": IncidentStatus.NEEDS_HUMAN.value,
            "terminal_reason": draft.reason or "Investigation budget exhausted",
        }

    async def _validate_hypotheses(self, draft: SynthesisDraft, incident_id: str) -> None:
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for hypothesis in draft.hypotheses
                for evidence_id in [
                    *hypothesis.supporting_evidence_ids,
                    *hypothesis.contradicting_evidence_ids,
                ]
            )
        )
        await self._load_evidence(evidence_ids, incident_id)

    async def _load_evidence(
        self,
        evidence_ids: list[str],
        incident_id: str,
    ) -> list[EvidenceRef]:
        evidence: list[EvidenceRef] = []
        for evidence_id in evidence_ids:
            item = await self._references.get_evidence(evidence_id)
            if item is None:
                raise DomainInvariantError(f"evidence does not exist: {evidence_id}")
            if item.incident_id != incident_id:
                raise DomainInvariantError("evidence belongs to another incident")
            evidence.append(item)
        return evidence
